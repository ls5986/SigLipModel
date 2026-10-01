"""Supabase is authoritative; no local dataset, SQLite or review JSON is required."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import threading
import time
from uuid import UUID

from acquisition_policy import supports_prior
from photo_view import effective_photo
from studio_data import FEATURES, StudioStore, now, trim_metadata, validate_review


class Database:
    def __init__(self, dsn, workspace):
        self.dsn, self.workspace = dsn, str(UUID(workspace))

    @contextmanager
    def connect(self):
        import psycopg
        from psycopg.rows import dict_row
        try:
            with psycopg.connect(self.dsn, sslmode='require', connect_timeout=10,
                                 row_factory=dict_row) as db:
                db.execute("SET LOCAL statement_timeout='15000ms'")
                yield db
        except psycopg.errors.SerializationFailure:
            raise RuntimeError('This record changed. Reload before saving.') from None
        except psycopg.Error:
            # Never send connection strings, SQL or server diagnostics to the browser.
            raise OSError('Training database request failed; no local fallback used') from None

    def verify(self):
        with self.connect() as db:
            row = db.execute('''SELECT r.rolsuper,r.rolbypassrls,
              acq_training.allowed_workspace(%s) AS allowed,
              to_regclass('acq_training.studio_state') IS NOT NULL AS installed
              FROM pg_roles r WHERE r.rolname=current_user''', (self.workspace,)).fetchone()
            if not row or row['rolsuper'] or row['rolbypassrls'] or not row['allowed'] or not row['installed']:
                raise ValueError('Use a restricted training login with workspace access and the Studio migration installed')

    def state(self, db, kind, identifier):
        row = db.execute('''SELECT payload,revision FROM acq_training.studio_state
                            WHERE workspace_id=%s AND kind=%s AND item_id=%s''',
                         (self.workspace, kind, identifier)).fetchone()
        return {**row['payload'], 'revision': row['revision']} if row else None

    def save(self, db, kind, identifier, expected, payload):
        from psycopg.types.json import Jsonb
        if type(expected) is not int or expected < 0:
            raise ValueError('Expected revision is required')
        row = db.execute('SELECT acq_training.save_studio_state(%s,%s,%s,%s,%s) AS revision',
                         (self.workspace, kind, identifier, expected, Jsonb(payload))).fetchone()
        return {**payload, 'revision': row['revision']}


class SupabaseStore:
    def __init__(self, database, storage):
        self.database, self.storage = database, storage
        self.workspace = database.workspace
        self.lock = threading.RLock()
        self._index = None

    def _examples(self, db, identifier):
        rows = db.execute('''SELECT e.id,e.listing_key,e.source_rows,e.source_snapshot,
             e.group_id,g.protected_test FROM acq_training.examples e
             JOIN acq_training.property_groups g ON g.workspace_id=e.workspace_id AND g.id=e.group_id
             WHERE e.workspace_id=%s AND e.listing_key=%s ORDER BY e.id''',
             (self.workspace, identifier)).fetchall()
        if not rows:
            raise ValueError('Unknown property')
        return rows

    @staticmethod
    def _selected(example):
        return next((c for c in example['source_snapshot'].get('mls_candidates', [])
                     if str(c.get('listing', {}).get('ListingKey')) == example['listing_key']), {})

    def _photos(self, db, identifier):
        rows = db.execute('''SELECT p.id,p.provider_media_key,p.image_sha256,p.storage_bucket,
               p.storage_object_key,p.context,p.context_evidence,e.group_id,
               (g.protected_test OR EXISTS (SELECT 1 FROM acq_training.photos related
                 JOIN acq_training.examples re ON (re.workspace_id,re.id)=(related.workspace_id,related.example_id)
                 JOIN acq_training.property_groups rg ON (rg.workspace_id,rg.id)=(re.workspace_id,re.group_id)
                 WHERE related.workspace_id=p.workspace_id AND related.image_sha256=p.image_sha256
                   AND rg.protected_test)) AS protected_test
             FROM acq_training.photos p
             JOIN acq_training.examples e ON (e.workspace_id,e.id)=(p.workspace_id,p.example_id)
             JOIN acq_training.property_groups g ON (g.workspace_id,g.id)=(e.workspace_id,e.group_id)
             WHERE e.workspace_id=%s AND e.listing_key=%s
               AND p.revoked_at IS NULL AND (p.retention_until IS NULL OR p.retention_until>now())
             ORDER BY p.provider_media_key,p.id''', (self.workspace, identifier)).fetchall()
        unique = {}
        for row in rows:
            key = identifier+':'+str(row['provider_media_key'])
            if key in unique and unique[key]['image_sha256'] != row['image_sha256']:
                raise ValueError('Conflicting saved photo versions require reconciliation')
            if key in unique:
                unique[key]['protected_test'] |= row['protected_test']
            else:
                unique[key] = dict(row, image_id=key)
        return sorted(unique.values(), key=lambda r:(r['context_evidence'].get('provider_metadata', {}).get('Order') or 0,r['image_id']))

    def _legacy(self, db, identifiers, fields=None):
        # Extract only this property's labels on the server, not the megabyte history document.
        rows = db.execute('''WITH d AS (
           SELECT payload FROM acq_training.migration_documents WHERE workspace_id=%s
             AND replace(logical_path,chr(92),'/')='data/human_reviews.json'
           ORDER BY migrated_at DESC,sha256 DESC LIMIT 1)
           SELECT f.field,k.item_id,d.payload->f.field->k.item_id AS value FROM d
           CROSS JOIN unnest(%s::text[]) AS f(field) CROSS JOIN unnest(%s::text[]) AS k(item_id)
           WHERE d.payload->f.field->k.item_id IS NOT NULL''',
           (self.workspace, fields or ['images','properties','room_preferences','evaluation_preferences','room_corrections'], identifiers)).fetchall()
        result = {}
        for row in rows:
            result.setdefault(row['field'], {})[row['item_id']] = row['value']
        return result

    def _reviews(self, db, identifiers):
        return {(r['kind'],r['item_id']):{**r['payload'],'revision':r['revision']}
                for r in db.execute('''SELECT kind,item_id,payload,revision FROM acq_training.studio_state
                  WHERE workspace_id=%s AND item_id=ANY(%s) AND kind IN ('image','property','era')''',
                  (self.workspace, identifiers))}

    @staticmethod
    def _review(kind, identifier, legacy, live):
        if (kind,identifier) in live:
            return live[kind,identifier]
        # Reuse the existing legacy approval rules without a local file/database.
        class Empty:
            def execute(self, *args): return self
            def fetchone(self): return None
        return StudioStore.reviews(None, Empty(), kind, identifier, legacy)

    def _history(self, examples, photos, review):
        supported = all(supports_prior(self._selected(e)) for e in examples)
        digest = hashlib.sha256(json.dumps(sorted(p['image_sha256'] for p in photos)).encode()).hexdigest()
        wrong = review and review.get('decision')=='wrong_era'
        blocked = bool(wrong or not supported)
        return {'source_rows':sorted({n for e in examples for n in e['source_rows']}),
                'source':examples[0]['source_snapshot'].get('spreadsheet', {}),
                'mls_listing':trim_metadata(self._selected(examples[0]).get('listing', {})),
                'evidence_hash':digest, 'review':review, 'blocked':blocked,
                'photo_coverage':review.get('photo_coverage','unknown') if review and review.get('evidence_hash')==digest else 'unknown',
                'acquisition_status':'wrong_era' if wrong else 'prior_acquisition_candidate' if supported else 'needs_prior_listing',
                'block_reason':'You flagged these photos as the wrong property or era.' if wrong else
                  'Earlier acquisition listing/photos need rematching.' if blocked else None,
                'timing_verified':bool(photos and review and review.get('decision')=='correct_era' and
                                       review.get('evidence_hash')==digest and not blocked),
                'trainable':False,'training_gate':'Review labels and protected groups remain separate checks'}

    def property(self, identifier):
        with self.database.connect() as db:
            examples, photos = self._examples(db,identifier), self._photos(db,identifier)
            ids = [identifier, *[p['image_id'] for p in photos]]
            legacy, live = self._legacy(db,ids), self._reviews(db,ids)
            proposal = self.database.state(db,'document','model-proposal:'+identifier) or {}
            automatic = self.database.state(db,'document','autolabel-result:'+identifier) or {}
        proposed_images = {i['image_id']:i for i in proposal.get('images',[]) if i.get('image_id')}
        from automatic_labels import POLICY
        automatic_images = {i['image_id']:i for i in automatic.get('images',[]) if i.get('image_id')} if automatic.get('policy')==POLICY else {}
        metadata = trim_metadata(self._selected(examples[0]).get('listing', {}))
        metadata['source_role'] = 'historical candidate'
        images, coverage = [], {r:'unknown' for r in ('kitchen','bathroom','living')}
        for p in photos:
            review = self._review('image',p['image_id'],legacy,live)
            machine = proposed_images.get(p['image_id'],{})
            if machine.get('sha256') != p['image_sha256']: machine = {}
            suggestion = automatic_images.get(p['image_id'],{})
            if suggestion.get('sha256') != p['image_sha256']: suggestion = {}
            if suggestion: machine = suggestion
            # Model suggestions remain suggestions; human corrections take priority.
            room = review.get('room') or machine.get('room') or 'other'
            if room in coverage:
                coverage[room] = 'confirmed' if review.get('status')=='approved' or review.get('room_confirmed') else 'suggested'
            context = p['context']
            provider = p['context_evidence'].get('provider_metadata', {})
            description = str(provider.get('LongDescription') or provider.get('ShortDescription') or '')
            if context=='unknown':
                text = description.casefold()
                context = 'shared_amenity' if any(s in text for s in ('community pool','community room','community exercise','hoa','clubhouse')) else 'floor_plan' if 'floor plan' in text or 'floorplan' in text else 'unknown'
            context = 'subject' if context in {'subject_interior','subject_exterior'} else context
            context_source = 'MLS description'
            if context=='unknown' and suggestion:
                context = suggestion.get('context','unknown')
                context_source = 'SigLIP suggestion'
            image = {'id':p['image_id'],'property_id':identifier,'room':room,'review':review,
                     'features':{**{f:None for f in FEATURES},**review.get('features', {})},
                     'room_source':'Human approved' if review.get('status')=='approved' else 'SigLIP suggestion (unsure)' if suggestion.get('uncertain') else 'SigLIP suggestion' if suggestion else 'Local model suggestion' if machine.get('room') else 'Unknown',
                     'suggestions':[machine] if machine else [],'local_model':None,'provider_context':context,'provider_description':description,
                     'provider_context_source':context_source,
                     'sha256':p['image_sha256'],'sequence':provider.get('Order'),
                     'split':'test' if p['protected_test'] else 'learning','training_allowed':not p['protected_test'],
                     'warnings':['Protected test group: evaluation only'] if p['protected_test'] else []}
            image['effective'] = effective_photo(image)
            images.append(image)
        return {'property':{'id':identifier,'address':metadata.get('UnparsedAddress',identifier),
                 'city':metadata.get('City'),'year_built':metadata.get('YearBuilt'),
                 'property_type':metadata.get('PropertySubType'),'metadata':metadata,
                 'review':self._review('property',identifier,legacy,live)},
                'images':images,'coverage':coverage,'property_suggestions':[], 'assessment':None,
                'historical_source':self._history(examples,photos,live.get(('era',identifier))),
                'capabilities':{'review':True,'assessment':False,'training':False,'autolabel':True,'storage':'supabase'}}

    def save_review(self, payload):
        kind, identifier = payload.get('kind'), payload.get('id')
        if kind not in {'image','property'} or not isinstance(identifier,str):
            raise ValueError('Known review kind and ID required')
        prop_id = identifier.rsplit(':',1)[0] if kind=='image' else identifier
        # Transaction holds property lock across era gate and review save.
        with self.database.connect() as db:
            self._lock_property(db, prop_id)
            examples, photos = self._examples(db,prop_id), self._photos(db,prop_id)
            era = self.database.state(db,'era',prop_id)
            if payload.get('status')=='approved' and self._history(examples,photos,era)['blocked']:
                raise ValueError('This acquisition evidence is quarantined. Review its photo era first.')
            source = next((p for p in photos if p['image_id']==identifier),None) if kind=='image' else examples[0]
            if source and kind=='image':
                source = {**source,'split':'test' if source['protected_test'] else 'learning'}
            current = self.database.state(db,kind,identifier)
            effective = dict(payload)
            if kind=='image' and 'context' not in effective and current and current.get('context'):
                effective['context'] = current['context']
            record = validate_review(effective,source)
            if kind=='property' and not set(record['standout_image_ids']) <= {
                photo['image_id'] for photo in photos
            }:
                raise ValueError('Standout photo belongs to another property')
            result = self.database.save(db,kind,identifier,payload.get('expected_revision'),record)
        self._index = None
        return result

    def _lock_property(self, db, identifier):
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (self.workspace+':'+identifier,))

    def review_era(self, payload):
        identifier = payload.get('property_id')
        if not isinstance(identifier,str): raise ValueError('Property ID required')
        if payload.get('decision') not in {'correct_era','wrong_era','unsure'}:
            raise ValueError('Invalid era decision')
        for key,limit in [('reviewer',100),('reason',2000)]:
            if not isinstance(payload.get(key),str) or not 1<=len(payload[key].strip())<=limit:
                raise ValueError('Reviewer and evidence reason required')
        with self.database.connect() as db:
            self._lock_property(db,identifier)
            history = self._history(self._examples(db,identifier), self._photos(db,identifier),
                                    self.database.state(db,'era',identifier))
            if payload.get('evidence_hash')!=history['evidence_hash']:
                raise RuntimeError('Photo evidence changed; reload before saving')
            if payload['decision']=='correct_era' and not all(supports_prior(self._selected(e)) for e in self._examples(db,identifier)):
                raise ValueError('Rematch the prior acquisition before approving its era')
            coverage = payload.get('photo_coverage',history['photo_coverage'])
            if coverage not in {'unknown','interior_available','no_interior'}:
                raise ValueError('Invalid photo coverage')
            record = {k:payload[k].strip() for k in ('decision','reviewer','reason')}
            record['photo_coverage'] = coverage
            record.update(property_id=identifier,evidence_hash=history['evidence_hash'],at=now())
            result = self.database.save(db,'era',identifier,payload.get('expected_revision'),record)
        self._index = None
        return result

    def image_path(self, identifier):
        if not isinstance(identifier,str) or ':' not in identifier: raise ValueError('Unknown image')
        property_id, media_key = identifier.rsplit(':',1)
        with self.database.connect() as db:
            photos = db.execute('''SELECT min(p.storage_bucket) AS storage_bucket,min(p.storage_object_key) AS storage_object_key,p.image_sha256
                FROM acq_training.photos p JOIN acq_training.examples e
                ON (e.workspace_id,e.id)=(p.workspace_id,p.example_id)
                WHERE e.workspace_id=%s AND e.listing_key=%s AND p.provider_media_key=%s
                  AND p.revoked_at IS NULL AND (p.retention_until IS NULL OR p.retention_until>now())
                GROUP BY p.image_sha256 LIMIT 2''', (self.workspace,property_id,media_key)).fetchall()
        if not photos: raise ValueError('Image unavailable or access expired')
        if len({p['image_sha256'] for p in photos})>1:
            raise ValueError('Conflicting saved photo versions require reconciliation')
        photo = photos[0]
        return self.storage.get(photo['storage_bucket'],photo['storage_object_key'],photo['image_sha256'])

    def queue(self, args):
        scope, queue = args.get('scope','acquisitions'), args.get('queue','all')
        if scope not in {'acquisitions','quarantine','reference','training'} or queue not in {'all','ready','unscored','reviewed','photo_match'}:
            raise ValueError('Unknown review queue')
        offset,limit = max(0,int(args.get('offset',0))),min(40,max(1,int(args.get('limit',20))))
        with self.lock:
            if self._index is None or time.monotonic()-self._index[0]>15:
                with self.database.connect() as db:
                    # Compact listing summary only: no full source snapshots, review history, or photo bytes.
                    rows = db.execute('''SELECT e.listing_key,e.source_rows,
                        c.item->'listing'->>'UnparsedAddress' AS address,
                        c.item->'listing'->>'City' AS city,
                        c.item->'listing'->>'ListingId' AS listing_id,
                        jsonb_build_object('listing',jsonb_build_object('StandardStatus',c.item->'listing'->>'StandardStatus'),
                          'match',c.item->'match') AS candidate
                      FROM acq_training.examples e
                      LEFT JOIN LATERAL (SELECT item FROM jsonb_array_elements(e.source_snapshot->'mls_candidates') item
                         WHERE item->'listing'->>'ListingKey'=e.listing_key LIMIT 1)c ON true
                      WHERE e.workspace_id=%s AND e.listing_key IS NOT NULL ORDER BY e.listing_key,e.id''',
                      (self.workspace,)).fetchall()
                    photos = {r['listing_key']:r for r in db.execute('''WITH unique_photos AS (
                       SELECT DISTINCT e.listing_key,p.provider_media_key,p.image_sha256
                       FROM acq_training.photos p JOIN acq_training.examples e
                       ON (e.workspace_id,e.id)=(p.workspace_id,p.example_id)
                       WHERE e.workspace_id=%s AND p.revoked_at IS NULL
                       AND (p.retention_until IS NULL OR p.retention_until>now()))
                       SELECT listing_key,count(*) AS count,min(listing_key||':'||provider_media_key) AS hero,
                       encode(sha256(convert_to(jsonb_agg(image_sha256 ORDER BY image_sha256)::text,'UTF8')),'hex') AS evidence_hash
                       FROM unique_photos GROUP BY listing_key''',(self.workspace,))}
                    states = {(r['kind'],r['item_id']):r['payload'] for r in db.execute('''SELECT kind,item_id,payload
                       FROM acq_training.studio_state WHERE workspace_id=%s AND kind IN ('property','era')''',(self.workspace,))}
                    legacy = self._legacy(db,sorted({r['listing_key'] for r in rows}), ['properties'])
                items = {}
                for row in rows:
                    key = row['listing_key']
                    blocked = not supports_prior(row['candidate'])
                    if key in items:
                        items[key]['blocked'] |= blocked
                        continue
                    review = states.get(('property',key),legacy.get('properties',{}).get(key,{}))
                    era = states.get(('era',key),{})
                    photo = photos.get(key,{})
                    items[key] = {'id':key,'address':row['address'] or key,'city':row['city'],
                        'listing_id':row['listing_id'],'image_count':photo.get('count',0),'hero_image_id':photo.get('hero'),
                        'status':'reviewed' if review.get('status')=='approved' else 'unscored',
                        'human_target':review.get('target_fit'),'target':None,
                        # Queue is conservative; property detail verifies exact evidence hash.
                        'needs_photo_match':not(photo.get('count') and era.get('decision')=='correct_era' and era.get('evidence_hash')==photo.get('evidence_hash')),'blocked':blocked or era.get('decision')=='wrong_era',
                        'source_role':'historical candidate','acquisition_status':'prior_acquisition_candidate'}
                for item in items.values():
                    if item['blocked']: item['acquisition_status']='needs_prior_listing'
                self._index = time.monotonic(),list(items.values())
            items = deepcopy(self._index[1])
        items = [i for i in items if scope=='training' or scope!='reference' and (i['blocked']==(scope=='quarantine'))]
        if scope=='training':
            cohort = self.document('training-cohort-acquisition-250-v1')
            keys = set((cohort or {}).get('listing_keys', []))
            items = [item for item in items if item['id'] in keys]
        counts = {'all':len(items),'ready':0,'unscored':sum(i['status']=='unscored' for i in items),
                  'reviewed':sum(i['status']=='reviewed' for i in items),'photo_match':sum(i['needs_photo_match'] for i in items)}
        photo_count = sum(i['image_count'] for i in items)
        search = args.get('search','').strip().casefold()
        items = [i for i in items if (queue=='all' or queue=='photo_match' and i['needs_photo_match'] or i['status']==queue)
                 and (not search or search in ' '.join(str(i[k] or '') for k in ('id','address','city','listing_id')).casefold())]
        items.sort(key=lambda i:(not i['image_count'],i['status']=='reviewed',i['address']))
        return {'items':items[offset:offset+limit],'counts':counts,'total':len(items),'offset':offset,'limit':limit,
                'photo_count':photo_count,
                'capabilities':{'review':True,'assessment':False,'training':False,'storage':'supabase'}}

    def document(self, key):
        with self.database.connect() as db:
            return self.database.state(db,'document',key)

    def save_document(self, key, payload, expected_revision):
        with self.database.connect() as db:
            return self.database.save(db,'document',key,expected_revision,payload)

    def autolabel_status(self, identifier):
        from datetime import datetime, timezone
        request = self.document('autolabel-request:'+identifier) or {}
        worker = self.document('autolabel-worker') or {}
        try:
            online = (datetime.now(timezone.utc)-datetime.fromisoformat(worker['at'])).total_seconds()<90 and worker['status'] in {'ready','running','loading'}
        except (KeyError, ValueError, TypeError): online = False
        return {'status':request.get('status','not_requested'),'worker_online':online,
                'worker_status':worker.get('status') if online else 'disconnected',
                'error':request.get('error')}

    def request_autolabel(self, payload):
        from automatic_labels import POLICY
        identifier = payload.get('property_id')
        if not isinstance(identifier,str): raise ValueError('Property ID required')
        with self.database.connect() as db:
            self._lock_property(db,identifier)
            sources, photos = self._examples(db,identifier), self._photos(db,identifier)
            if not photos: raise ValueError('No retained photos to label')
            digest = self._history(sources,photos,None)['evidence_hash']
            result = self.database.state(db,'document','autolabel-result:'+identifier) or {}
            key = 'autolabel-request:'+identifier
            current = self.database.state(db,'document',key) or {}
            if result.get('evidence_hash')==digest and result.get('policy')==POLICY:
                return {'status':'completed'}
            if current.get('evidence_hash')==digest and current.get('policy')==POLICY and current.get('status') in {'queued','running'}:
                return {'status':current['status']}
            self.database.save(db,'document',key,current.get('revision',0),{
                'property_id':identifier,'evidence_hash':digest,'policy':POLICY,
                'status':'queued','at':now()})
        return {'status':'queued'}

    def autolabel_pending(self):
        with self.database.connect() as db:
            rows = db.execute('''SELECT payload FROM acq_training.studio_state
                WHERE workspace_id=%s AND kind='document' AND item_id LIKE 'autolabel-request:%%'
                AND payload->>'status' IN ('queued','running') ORDER BY updated_at LIMIT 50''',(self.workspace,)).fetchall()
        return [r['payload']['property_id'] for r in rows]

    def training_readiness(self):
        from cloud_training import readiness, snapshot
        _, properties = snapshot(self)
        return readiness(properties)

    def source_rows(self, args):
        """Full workbook ledger; unresolved rows remain visible and never train."""
        offset = max(0, int(args.get('offset', 0)))
        limit = min(40, max(1, int(args.get('limit', 20))))
        search = args.get('search', '').strip().casefold()
        state_filter = args.get('status', 'all')
        if state_filter not in {'all','verified','verify','rematch','missing_photos'}:
            raise ValueError('Unknown source-row status')
        with self.database.connect() as db:
            rows = db.execute('''SELECT e.id,e.listing_key,e.source_rows,e.source_snapshot,
                (SELECT count(*) FROM acq_training.photos p WHERE
                 (p.workspace_id,p.example_id)=(e.workspace_id,e.id) AND p.revoked_at IS NULL
                 AND (p.retention_until IS NULL OR p.retention_until>now())) AS photo_count
                FROM acq_training.examples e WHERE e.workspace_id=%s ORDER BY e.id''',
                (self.workspace,)).fetchall()
            states = {(r['kind'],r['item_id']):{**r['payload'],'revision':r['revision']} for r in db.execute('''
                SELECT kind,item_id,payload,revision FROM acq_training.studio_state
                WHERE workspace_id=%s AND kind IN ('era','document')''',(self.workspace,))}
            photo_rows = db.execute('''SELECT e.listing_key,p.provider_media_key,p.image_sha256
                FROM acq_training.photos p JOIN acq_training.examples e
                ON (p.workspace_id,p.example_id)=(e.workspace_id,e.id)
                WHERE p.workspace_id=%s AND p.revoked_at IS NULL
                AND (p.retention_until IS NULL OR p.retention_until>now())''',(self.workspace,)).fetchall()
        by_listing = {}
        for photo in photo_rows:
            by_listing.setdefault(photo['listing_key'],{}).setdefault(photo['provider_media_key'],set()).add(photo['image_sha256'])
        supported = {}
        for row in rows:
            supported[row['listing_key']] = supported.get(row['listing_key'],True) and supports_prior(self._selected(row))
        items = []
        for row in rows:
            selected = self._selected(row)
            source = trim_metadata(row['source_snapshot'].get('spreadsheet', {}))
            era = states.get(('era',row['listing_key']),{})
            attached = by_listing.get(row['listing_key'],{})
            photo_count = len(attached)
            status = 'rematch' if not supported[row['listing_key']] or era.get('decision')=='wrong_era' else (
                'missing_photos' if not photo_count else 'verify')
            # Detail verifies the exact photo-byte hash; an old approval alone is insufficient.
            if status=='verify' and era.get('decision')=='correct_era':
                digest = hashlib.sha256(json.dumps(sorted(next(iter(hashes)) for hashes in attached.values())).encode()).hexdigest()
                if all(len(hashes)==1 for hashes in attached.values()) and era.get('evidence_hash')==digest: status='verified'
            for source_row in row['source_rows']:
                note = states.get(('document','source-row:'+str(source_row)),{})
                items.append({'source_row':source_row,'listing_key':row['listing_key'],
                    'address':source.get('Address') or source.get('UnparsedAddress') or selected.get('listing',{}).get('UnparsedAddress') or 'Unresolved source row',
                    'source':source,'status':status,'photo_count':photo_count,
                    'photo_coverage':era.get('photo_coverage','unknown') if status=='verified' else 'unknown',
                    'verification_note':note,
                    'candidates':[{'listing':trim_metadata(c.get('listing',{})),
                                   'match':c.get('match',{}),'prior_supported':supports_prior(c)}
                                  for c in row['source_snapshot'].get('mls_candidates',[])]})
        # Source rows identify workbook records; do not collapse repeated parcels.
        unique = {item['source_row']:item for item in items}
        items = sorted(unique.values(),key=lambda item:item['source_row'])
        counts = {state:sum(i['status']==state for i in items) for state in ('verified','verify','rematch','missing_photos')}
        total_rows = len(items)
        items = [i for i in items if (state_filter=='all' or i['status']==state_filter)
                 and (not search or search in (str(i['source_row'])+' '+i['address']+' '+str(i['listing_key'])).casefold())]
        return {'items':items[offset:offset+limit],'counts':counts,'source_rows':total_rows,
                'total':len(items),'offset':offset,'limit':limit}

    def source_row_note(self, payload):
        source_row = payload.get('source_row')
        if type(source_row) is not int or source_row < 1:
            raise ValueError('Valid source row required')
        reviewer, note = payload.get('reviewer',''), payload.get('note','')
        if not isinstance(reviewer,str) or not 1<=len(reviewer.strip())<=100 or not isinstance(note,str) or not 1<=len(note.strip())<=2000:
            raise ValueError('Reviewer and a short correction note are required')
        with self.database.connect() as db:
            exists = db.execute('SELECT 1 FROM acq_training.examples WHERE workspace_id=%s AND %s=ANY(source_rows) LIMIT 1',
                                (self.workspace,source_row)).fetchone()
            if not exists: raise ValueError('Unknown workbook row')
            return self.database.save(db,'document','source-row:'+str(source_row),payload.get('expected_revision'),
                                      {'source_row':source_row,'reviewer':reviewer.strip(),'note':note.strip(),'at':now()})

    def apply_proposals(self, output):
        # Editable machine suggestions never overwrite reviewed labels.
        for proposal in output:
            key = 'model-proposal:'+proposal['property_id']
            current = self.document(key)
            self.save_document(key, proposal, (current or {}).get('revision', 0))
