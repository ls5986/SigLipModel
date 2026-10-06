"""Supabase is authoritative; no local dataset, SQLite or review JSON is required."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import threading
import time
from uuid import UUID

from acquisition_policy import supports_prior, first_sale_policy, annotate_first_sales
from photo_view import effective_photo
from studio_data import FEATURES, StudioStore, now, trim_metadata, validate_review


def validation_candidate(example):
    candidates = example.get('source_snapshot',{}).get('mls_candidates',[])
    selected = str(example.get('listing_key') or '')
    if selected:
        match = next((c for c in candidates if str(c.get('listing',{}).get('ListingKey'))==selected),None)
        if match: return match
    def rank(candidate):
        listing,match = candidate.get('listing',{}),candidate.get('match',{})
        agreements = match.get('sale_agreements',[])
        prior = next((a for a in agreements if a.get('source_sale')=='prior'),{})
        gap = prior.get('minimum_date_gap_days')
        price_gap = prior.get('price_gap_dollars')
        return (
            int(match.get('rank_score') or 0),
            int(bool(match.get('exact_apn'))),
            int(bool(match.get('street_number_matches'))),
            int(not bool(match.get('unit_conflict'))),
            int(listing.get('CloseDate') is not None),
            -(gap if type(gap) in {int,float} else 10**9),
            -(price_gap if type(price_gap) in {int,float} else 10**12),
            str(listing.get('ListingKey') or ''),
        )
    return max(candidates,key=rank,default={})


def validation_photo_gallery(detail, limit=8):
    retained = [
        image for image in detail.get('images',[])
        if image.get('selection',{}).get('included',True)
        and image.get('effective',{}).get('context') not in {'shared_amenity','floor_plan','unrelated'}
    ]
    interiors = [
        image for image in retained
        if image.get('effective',{}).get('room') in {'kitchen','bathroom','living','bedroom','other'}
    ]
    selected = []
    for room in ('kitchen','bathroom'):
        match = next((image for image in interiors
                      if image.get('effective',{}).get('room')==room and image not in selected),None)
        if match: selected.append(match)
    for image in interiors:
        if len(selected)>=limit: break
        if image not in selected: selected.append(image)
    for image in retained:
        if len(selected)>=limit: break
        if image not in selected: selected.append(image)
    return selected[:limit]


def validation_photo_pair(detail):
    return validation_photo_gallery(detail, 2)


def validation_room(metadata):
    text = ' '.join(str(metadata.get(key) or '') for key in
                    ('ShortDescription','LongDescription','ImageOf','MediaCategory')).casefold()
    if 'kitchen' in text: return 'kitchen'
    if any(term in text for term in ('bathroom','bath ','shower','tub','toilet','vanity')):
        return 'bathroom'
    if 'bedroom' in text: return 'bedroom'
    if any(term in text for term in ('living room','family room','great room')):
        return 'living'
    if any(term in text for term in ('exterior','front view','rear view','yard','patio','pool')):
        return 'exterior'
    return 'property'


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
             e.group_id,g.protected_test,
             (SELECT min(left(sale.value,10)) FROM acq_training.examples sibling
              CROSS JOIN LATERAL jsonb_each_text(sibling.source_snapshot->'spreadsheet') sale
              WHERE sibling.workspace_id=e.workspace_id AND sibling.group_id=e.group_id
              AND sale.key IN ('Prior Sale Date','Last Sale Date') AND sale.value ~ '^2026-[0-9]{2}-[0-9]{2}') AS first_sale_date FROM acq_training.examples e
             JOIN acq_training.property_groups g ON g.workspace_id=e.workspace_id AND g.id=e.group_id
             WHERE e.workspace_id=%s AND e.listing_key=%s ORDER BY e.id''',
             (self.workspace, identifier)).fetchall()
        if not rows:
            raise ValueError('Unknown property')
        return rows

    @staticmethod
    def _selected(example):
        candidate = next((c for c in example['source_snapshot'].get('mls_candidates', [])
                     if str(c.get('listing', {}).get('ListingKey')) == example['listing_key']), {})
        if candidate and example.get('first_sale_date'):
            candidate = {**candidate,'match':{**candidate.get('match',{}),'first_actual_sale_date_2026':example['first_sale_date']}}
        return candidate

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

    def validation_image_path(self, identifier):
        try:
            _,example_id,media_key = identifier.split(':',2)
            example_uuid = UUID(example_id)
        except (ValueError,TypeError):
            raise ValueError('Unknown validation image') from None
        with self.database.connect() as db:
            example = self._validation_example(db,example_uuid)
            state = self.database.state(
                db,'document','mls-validation-media:'+str(example['id'])
            ) or {}
        rows = [
            row for row in state.get('images',[])
            if str(row.get('provider_media_key'))==media_key
        ]
        if len(rows)!=1: raise ValueError('Validation image unavailable or ambiguous')
        row = rows[0]
        return self.storage.get(row['storage_bucket'],row['storage_object_key'],row['image_sha256'])

    def _reviews(self, db, identifiers):
        return {(r['kind'],r['item_id']):{**r['payload'],'revision':r['revision']}
                for r in db.execute('''SELECT kind,item_id,payload,revision FROM acq_training.studio_state
                  WHERE workspace_id=%s AND item_id=ANY(%s) AND kind IN ('image','property','era','document')''',
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
        supported = all(supports_prior(self._selected(e),e['source_snapshot'].get('spreadsheet'),e['source_snapshot'].get('mls_candidates')) for e in examples)
        digest = hashlib.sha256(json.dumps(sorted(p['image_sha256'] for p in photos)).encode()).hexdigest()
        policy = first_sale_policy(self._selected(examples[0]),examples[0]['source_snapshot'].get('spreadsheet'),examples[0]['source_snapshot'].get('mls_candidates'))
        wrong = review and review.get('decision')=='wrong_era'
        blocked = bool(wrong or not supported)
        return {'sale_policy':policy,
                'source_rows':sorted({n for e in examples for n in e['source_rows']}),
                'source':examples[0]['source_snapshot'].get('spreadsheet', {}),
                'mls_listing':trim_metadata(self._selected(examples[0]).get('listing', {})),
                'evidence_hash':digest, 'review':review, 'blocked':blocked,
                'photo_coverage':review.get('photo_coverage','unknown') if review and review.get('evidence_hash')==digest else 'unknown',
                'acquisition_status':'wrong_era' if wrong else 'prior_acquisition_candidate' if supported else 'needs_prior_listing',
                'block_reason':'You flagged these photos as the wrong property or era.' if wrong else
                  policy.get('reason') or 'First acquisition listing/photos need rematching.' if blocked else None,
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
        from automatic_labels import active_policy
        POLICY = active_policy()
        automatic_images = {i['image_id']:i for i in automatic.get('images',[]) if i.get('image_id')} if automatic.get('policy')==POLICY else {}
        listing = self._selected(examples[0]).get('listing', {})
        metadata = trim_metadata(listing)
        metadata['source_role'] = 'historical candidate'
        history = self._history(examples,photos,live.get(('era',identifier)))
        mismatched = bool(history['sale_policy']['target_sale_date'] and not history['sale_policy']['supported'])
        images, coverage = [], {r:'unknown' for r in ('kitchen','bathroom','living')}
        # Retained photos remain visible as source-review references. Their
        # acquisition eligibility is enforced separately, not by hiding them.
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
                context_source = suggestion.get('context_source') or ('OpenAI draft' if suggestion.get('policy','').startswith('openai-') else 'SigLIP suggestion')
            image = {'id':p['image_id'],'property_id':identifier,'room':room,'review':review,
                     'features':{**{f:None for f in FEATURES},**suggestion.get('features',{}),**review.get('features', {})},
                     'condition_draft':suggestion.get('condition_label','unknown'),
                     'room_source':'Human approved' if review.get('status')=='approved' else 'SigLIP suggestion' if suggestion.get('room_source')=='SigLIP' else 'OpenAI draft' if suggestion.get('policy','').startswith('openai-') else 'SigLIP suggestion (unsure)' if suggestion.get('uncertain') else 'SigLIP suggestion' if suggestion else 'Local model suggestion' if machine.get('room') else 'Unknown',
                     'suggestions':[machine] if machine else [],'local_model':None,'provider_context':context,'provider_description':description,
                     'provider_context_source':context_source,
                     'sha256':p['image_sha256'],'sequence':provider.get('Order'),
                     'split':'test' if p['protected_test'] else 'learning','training_allowed':not p['protected_test'] and not history['blocked'],
                     'source_quarantined':history['blocked'],
                     'warnings':['Protected test group: evaluation only'] if p['protected_test'] else []}
            image['effective'] = effective_photo(image)
            from photo_selection import selection
            from listing_text import image_evidence
            image['synthetic_evidence'] = image_evidence(listing,description)
            image['selection'] = selection(image['effective']['context'],review,image['synthetic_evidence'])
            images.append(image)
        return {'property':{'id':identifier,'address':metadata.get('UnparsedAddress',identifier),
                 'city':metadata.get('City'),'year_built':metadata.get('YearBuilt'),
                 'property_type':metadata.get('PropertySubType'),'metadata':metadata,
                 'review':self._review('property',identifier,legacy,live),
                 'mls_remarks':__import__('listing_text').remarks(listing),
                 'synthetic_evidence':__import__('listing_text').image_evidence(listing)},
                'images':images,'stored_photo_count':len(photos),'photo_display_notice':'Stored listing photos are shown for source review only; acquisition-era matching is unresolved.' if history['blocked'] and photos else None,'coverage':coverage,'property_suggestions':[], 'assessment':None,
                'historical_source':history,
                'capabilities':{'review':True,'assessment':False,'training':False,'autolabel':not mismatched and any(not i['synthetic_evidence']['excluded'] for i in images),'storage':'supabase'}}

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
            if payload.get('label_schema_version'):
                from studio_v2 import label_evidence, validate_text_reviews
                detail = self.property(identifier)
                prop = detail['property']
                expected_evidence = label_evidence(
                    identifier, prop.get('mls_remarks') or '', detail['images'], prop.get('metadata'),
                )
                if payload.get('label_evidence_id') != expected_evidence:
                    raise RuntimeError('Evidence changed; reload before saving labels')
                validate_text_reviews(payload.get('text_signals', []), prop.get('mls_remarks') or '')
            effective = dict(payload)
            if kind=='image' and 'context' not in effective and current and current.get('context'):
                effective['context'] = current['context']
            record = validate_review(effective,source)
            if kind=='property' and not set(record['standout_image_ids']) <= {
                photo['image_id'] for photo in photos
            }:
                raise ValueError('Standout photo belongs to another property')
            result = self.database.save(db,kind,identifier,payload.get('expected_revision'),record)
            if payload.get('label_schema_version'):
                from psycopg.types.json import Jsonb
                reviewed_signals = {item['signal'] for item in record['text_signals']}
                retractions = [
                    {'signal': item['signal'], 'state': 'UNKNOWN', 'probability': None,
                     'snippet': None, 'start': None, 'end': None}
                    for item in (current or {}).get('text_signals', [])
                    if item['signal'] not in reviewed_signals
                ]
                answers = [
                    ('property_condition', {'value': record.get('physical_condition', 'UNKNOWN')}),
                    ('property_modernization', {'value': record.get('modernization_state', 'UNKNOWN')}),
                    ('property_target', {'value': record.get('target_fit') or 'unsure', 'fit_basis': record.get('fit_basis')}),
                    *[('property_text_signal', item) for item in [*record['text_signals'], *retractions]],
                ]
                for task, answer in answers:
                    previous = db.execute('''SELECT id FROM acq_training.review_events
                        WHERE workspace_id=%s AND example_id=%s AND task=%s
                          AND (%s::text IS NULL OR answer->>'signal'=%s)
                        ORDER BY created_at DESC,id DESC LIMIT 1''',
                        (self.workspace, examples[0]['id'], task, answer.get('signal'), answer.get('signal'))).fetchone()
                    db.execute('''INSERT INTO acq_training.review_events
                        (workspace_id,example_id,task,answer,status,reviewer_id,prediction_was_visible,
                         prior_event_id,label_schema_version,source_evidence)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                        (self.workspace, examples[0]['id'], task, Jsonb(answer), record['status'],
                         record['reviewer'], bool(record.get('assistant_proposal')), previous['id'] if previous else None,
                         record['label_schema_version'], Jsonb({
                             'label_evidence_id': expected_evidence, 'property_id': identifier,
                             'assistant_proposal': record.get('assistant_proposal'),
                             'remarks_sha256': hashlib.sha256((prop.get('mls_remarks') or '').encode()).hexdigest(),
                             'photo_hashes': [{'id': image['id'], 'sha256': image.get('sha256')}
                                              for image in detail['images']],
                         })))
        self._index = None
        return result

    def complete_review(self, payload):
        identifier = payload.get('property_id')
        decision = payload.get('decision')
        reviewer = payload.get('reviewer')
        reason = payload.get('reason')
        if not isinstance(identifier,str) or decision not in {'correct_era','wrong_era','unsure'}:
            raise ValueError('Known property and listing/photo decision required')
        if not isinstance(reviewer,str) or not 1<=len(reviewer.strip())<=100:
            raise ValueError('Reviewer name required')
        if not isinstance(reason,str) or not 1<=len(reason.strip())<=2000:
            raise ValueError('Short review reason required')
        coverage = payload.get('photo_coverage','unknown')
        if coverage not in {'unknown','interior_available','no_interior'}:
            raise ValueError('Invalid photo coverage')
        with self.database.connect() as db:
            self._lock_property(db,identifier)
            examples,photos = self._examples(db,identifier),self._photos(db,identifier)
            current_era = self.database.state(db,'era',identifier)
            history = self._history(examples,photos,current_era)
            if payload.get('evidence_hash')!=history['evidence_hash']:
                raise RuntimeError('Photo evidence changed; reload before saving')
            if decision=='correct_era' and not all(
                supports_prior(self._selected(example),example['source_snapshot'].get('spreadsheet'),
                               example['source_snapshot'].get('mls_candidates'))
                for example in examples
            ):
                raise ValueError('Rematch the prior acquisition before approving its era')
            era_record = {
                'property_id':identifier,'decision':decision,'reviewer':reviewer.strip(),
                'reason':reason.strip(),'photo_coverage':coverage,
                'evidence_hash':history['evidence_hash'],'at':now(),
            }
            era_result = self.database.save(
                db,'era',identifier,payload.get('expected_era_revision'),era_record
            )
            property_result = None
            if decision=='correct_era':
                property_payload = payload.get('property_review')
                if not isinstance(property_payload,dict):
                    raise ValueError('Condition and opportunity review required for confirmed evidence')
                effective = {
                    **property_payload,'kind':'property','id':identifier,
                    'reviewer':reviewer.strip(),'status':'approved',
                }
                record = validate_review(effective,examples[0])
                if not set(record['standout_image_ids']) <= {photo['image_id'] for photo in photos}:
                    raise ValueError('Standout photo belongs to another property')
                property_result = self.database.save(
                    db,'property',identifier,property_payload.get('expected_revision'),record
                )
        self._index = None
        return {'era':era_result,'property':property_result,'complete':property_result is not None}

    def save_photo_selection(self, payload):
        identifier, reviewer = payload.get('id'), payload.get('reviewer')
        if not isinstance(identifier,str) or ':' not in identifier or type(payload.get('included')) is not bool:
            raise ValueError('Known photo and checkbox selection required')
        if not isinstance(reviewer,str) or not 1 <= len(reviewer.strip()) <= 100:
            raise ValueError('Reviewer name required')
        prop_id = identifier.rsplit(':',1)[0]
        with self.database.connect() as db:
            self._lock_property(db,prop_id)
            if not any(p['image_id']==identifier for p in self._photos(db,prop_id)):
                raise ValueError('Unknown retained photo')
            current = self.database.state(db,'image',identifier)
            if current is None:
                prior = self._review('image',identifier,self._legacy(db,[identifier]),{})
                current = prior if prior.get('status') in {'approved','draft'} else {}
            result = self.database.save(db,'image',identifier,payload.get('expected_revision'),{
                **current,'include_in_similarity':payload['included'],
                'selection_reviewer':reviewer.strip(),'selection_updated_at':now()})
        return result

    def _lock_property(self, db, identifier):
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (self.workspace+':'+identifier,))

    def review_era(self, payload):
        identifier = payload.get('property_id')
        label_provider = payload.get('label_provider','openai')
        if label_provider not in {'openai','copilot'}: raise ValueError('Unknown draft label provider')
        full = payload.get('all_photos') is True
        if full and label_provider!='copilot': raise ValueError('Full local batch requires Copilot')
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
            if payload['decision']=='correct_era' and not all(supports_prior(self._selected(e),e['source_snapshot'].get('spreadsheet'),e['source_snapshot'].get('mls_candidates')) for e in self._examples(db,identifier)):
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
        if isinstance(identifier,str) and identifier.startswith('validation:'):
            return self.validation_image_path(identifier)
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
        evidence_filter = args.get('evidence','all')
        if scope not in {'all','acquisitions','quarantine','reference','training'} or queue not in {
            'all','ready','unscored','reviewed','photo_match','tagged','todo','opportunity','complete','missing_text'
        }:
            raise ValueError('Unknown review queue')
        if evidence_filter not in {'all','interior','limited','metadata_only'}:
            raise ValueError('Unknown evidence filter')
        offset,limit = max(0,int(args.get('offset',0))),min(40,max(1,int(args.get('limit',20))))
        with self.lock:
            if self._index is None or time.monotonic()-self._index[0]>15:
                with self.database.connect() as db:
                    inventory = dict(db.execute('''SELECT count(*) AS imported_rows,
                        count(DISTINCT group_id) AS physical_groups,
                        count(DISTINCT listing_key) AS matched_listings,
                        count(*) FILTER (WHERE match_status='confirmed') AS confirmed_match_rows,
                        count(DISTINCT listing_key) FILTER (WHERE match_status='confirmed') AS confirmed_match_listings,
                        count(*) FILTER (WHERE listing_key IS NULL) AS unresolved_rows
                        FROM acq_training.examples WHERE workspace_id=%s''', (self.workspace,)).fetchone())
                    inventory['human_source_confirmations'] = db.execute('''SELECT count(*) AS count
                        FROM acq_training.studio_state WHERE workspace_id=%s AND kind='document'
                        AND item_id LIKE 'mls-validation:%%' AND payload->>'decision'='confirmed'
                        ''', (self.workspace,)).fetchone()['count']
                    # Compact listing summary only: no full source snapshots, review history, or photo bytes.
                    rows = db.execute('''SELECT e.listing_key,e.source_rows,e.group_id,
                        c.item->'listing'->>'UnparsedAddress' AS address,
                        c.item->'listing'->>'City' AS city,
                        c.item->'listing'->>'ListingId' AS listing_id,
                        c.item->'listing'->>'PhotosCount' AS provider_photo_count,
                        jsonb_build_object('listing',jsonb_build_object('StandardStatus',c.item->'listing'->>'StandardStatus','CloseDate',c.item->'listing'->>'CloseDate'),
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
                       FROM acq_training.studio_state WHERE workspace_id=%s AND (
                         kind IN ('property','era') OR kind='document' AND (
                           item_id LIKE 'autolabel-request:%%' OR item_id LIKE 'autolabel-result:%%'
                         )
                       )''',(self.workspace,))}
                    legacy = self._legacy(db,sorted({r['listing_key'] for r in rows}), ['properties'])
                annotate_first_sales(rows)
                items = {}
                for row in rows:
                    key = row['listing_key']
                    blocked = not supports_prior(row['candidate'])
                    if key in items:
                        items[key]['blocked'] |= blocked
                        continue
                    review = states.get(('property',key),legacy.get('properties',{}).get(key,{}))
                    era = states.get(('era',key),{})
                    label_request = states.get(('document','autolabel-request:'+key),{})
                    label_result = states.get(('document','autolabel-result:'+key),{})
                    tagged_images = [image for image in label_result.get('images', [])
                                     if isinstance(image, dict) and image.get('image_id')]
                    interior_tags = [
                        image for image in tagged_images
                        if image.get('context') in {'subject','subject_interior'}
                        and image.get('room') in {'kitchen','bathroom','living','bedroom'}
                    ]
                    photo = photos.get(key,{})
                    evidence_mode = (
                        'interior' if interior_tags else
                        'limited' if photo.get('count',0) else 'metadata_only'
                    )
                    try:
                        provider_photo_count = int(row['provider_photo_count']) if row['provider_photo_count'] not in {None, ''} else None
                    except (TypeError, ValueError):
                        provider_photo_count = None
                    retained_count = photo.get('count',0)
                    if blocked:
                        photo_state = 'source_conflict'
                    elif retained_count == 0 and provider_photo_count == 0:
                        photo_state = 'provider_no_photos'
                    elif retained_count == 0 and provider_photo_count and provider_photo_count > 0:
                        photo_state = 'provider_photos_not_imported'
                    elif era.get('decision') == 'wrong_era':
                        photo_state = 'wrong_acquisition_era'
                    elif retained_count and era.get('decision') != 'correct_era':
                        photo_state = 'acquisition_era_unverified'
                    elif retained_count and era.get('evidence_hash') != photo.get('evidence_hash'):
                        photo_state = 'photo_inventory_changed'
                    elif retained_count:
                        photo_state = 'photos_verified'
                    else:
                        photo_state = 'photo_status_unknown'
                    items[key] = {'id':key,'address':row['address'] or key,'city':row['city'],
                        'listing_id':row['listing_id'],'image_count':retained_count,'hero_image_id':photo.get('hero'),
                        'provider_photo_count':provider_photo_count,
                        'photo_status':{'state':photo_state,'provider_photo_count':provider_photo_count,
                            'retained_photo_count':retained_count,
                            'era_decision':era.get('decision'),'retained_evidence_hash':photo.get('evidence_hash'),
                            'verified_evidence_hash':era.get('evidence_hash')},
                        'status':'reviewed' if review.get('status')=='approved' else 'unscored',
                        'human_target':review.get('target_fit'),
                        'human_score':review.get('target_score'),'target':None,
                        'missing_text':not bool(review.get('text_signals')),
                        # Queue is conservative; property detail verifies exact evidence hash.
                        'needs_photo_match':not(photo.get('count') and era.get('decision')=='correct_era' and era.get('evidence_hash')==photo.get('evidence_hash')),'blocked':blocked or era.get('decision')=='wrong_era',
                        'autolabel_status':label_request.get('status','not_requested'),
                        'tagged_photo_count':len(tagged_images),
                        'interior_tagged_count':len(interior_tags),
                        'evidence_mode':evidence_mode,
                        'source_role':'historical candidate','acquisition_status':'prior_acquisition_candidate'}
                    items[key]['review_complete'] = (
                        not items[key]['needs_photo_match'] and items[key]['status']=='reviewed'
                    )
                for item in items.values():
                    if item['blocked']: item['acquisition_status']='needs_prior_listing'
                self._index = time.monotonic(),list(items.values()),inventory
            items = deepcopy(self._index[1])
        items = [i for i in items if scope in {'all','training'} or scope!='reference' and (i['blocked']==(scope=='quarantine'))]
        if scope=='training':
            cohort = self.document('training-cohort-acquisition-250-v1')
            keys = set((cohort or {}).get('listing_keys', []))
            items = [item for item in items if item['id'] in keys]
        counts = {'all':len(items),'ready':0,'unscored':sum(i['status']=='unscored' for i in items),
                  'reviewed':sum(i['status']=='reviewed' for i in items),'photo_match':sum(i['needs_photo_match'] for i in items)}
        counts['verified'] = sum(not i['needs_photo_match'] for i in items)
        counts['source_conflicts'] = sum(i['blocked'] for i in items)
        counts['opportunity'] = sum(
            not i['needs_photo_match'] and i['status']!='reviewed' for i in items
        )
        counts['complete'] = sum(i['review_complete'] for i in items)
        counts['todo'] = sum(not i['review_complete'] for i in items)
        counts['tagged'] = sum(
            i['autolabel_status']=='completed' and i['tagged_photo_count'] > 0 for i in items
        )
        counts['interior'] = sum(i['evidence_mode']=='interior' for i in items)
        counts['limited'] = sum(i['evidence_mode']=='limited' for i in items)
        counts['metadata_only'] = sum(i['evidence_mode']=='metadata_only' for i in items)
        counts['missing_text'] = sum(i['missing_text'] for i in items)
        photo_count = sum(i['image_count'] for i in items)
        search = args.get('search','').strip().casefold()
        items = [i for i in items if (queue=='all' or queue=='todo' and not i['review_complete']
                  or queue=='opportunity' and not i['needs_photo_match'] and i['status']!='reviewed'
                  or queue=='complete' and i['review_complete']
                  or queue=='photo_match' and i['needs_photo_match']
                  or queue=='tagged' and i['autolabel_status']=='completed' and i['tagged_photo_count']
                  or queue=='missing_text' and i['missing_text']
                  or i['status']==queue)
                 and (evidence_filter=='all' or evidence_filter=='limited' and i['evidence_mode']!='interior'
                      or i['evidence_mode']==evidence_filter)
                 and (not search or search in ' '.join(str(i[k] or '') for k in ('id','address','city','listing_id')).casefold())]
        evidence_priority = {'metadata_only':0,'limited':1,'interior':2}
        items.sort(key=lambda i:(i['review_complete'],not i['needs_photo_match'],
                                evidence_priority[i['evidence_mode']],i['address']))
        return {'items':items[offset:offset+limit],'counts':counts,'total':len(items),'offset':offset,'limit':limit,
                'photo_count':photo_count,'inventory':deepcopy(self._index[2]),
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
        worker_key = 'autolabel-room-worker' if request.get('stage','rooms')=='rooms' and request.get('policy','').startswith('siglip-rooms-openai-') else 'autolabel-copilot-worker' if request.get('label_provider')=='copilot' else 'autolabel-worker'
        worker = self.document(worker_key) or {}
        try:
            online = (datetime.now(timezone.utc)-datetime.fromisoformat(worker['at'])).total_seconds()<90 and worker['status'] in {'ready','running','loading'}
        except (KeyError, ValueError, TypeError): online = False
        return {'status':request.get('status','not_requested'),'worker_online':online,
                'worker_status':worker.get('status') if online or worker.get('status')=='unconfigured' else 'disconnected',
                'label_provider':request.get('label_provider','openai'),'stage':request.get('stage'),'mode':request.get('mode'),'test_photos':request.get('test_photos',0),
                'usage':request.get('usage'),
                'error':request.get('error')}

    def request_autolabel(self, payload):
        from automatic_labels import active_policy
        POLICY = active_policy()
        identifier = payload.get('property_id')
        label_provider = payload.get('label_provider','openai')
        if label_provider not in {'openai','copilot'}: raise ValueError('Unknown draft label provider')
        full = payload.get('all_photos') is True
        if full and label_provider!='copilot': raise ValueError('Full local batch requires Copilot')
        if not isinstance(identifier,str): raise ValueError('Property ID required')
        with self.database.connect() as db:
            self._lock_property(db,identifier)
            sources, photos = self._examples(db,identifier), self._photos(db,identifier)
            if not photos: raise ValueError('No retained photos to label')
            from listing_text import image_evidence
            if image_evidence(self._selected(sources[0]).get('listing',{}))['excluded']:
                raise ValueError('MLS discloses AI imagery or virtual staging. Identify original photos before labeling.')
            if not all(supports_prior(self._selected(r),r['source_snapshot'].get('spreadsheet'),r['source_snapshot'].get('mls_candidates')) for r in sources):
                raise ValueError('Correct first-sale listing/photos required before labeling')
            history = self._history(sources,photos,self.database.state(db,'era',identifier))
            if history['blocked']: raise ValueError('Wrong property or acquisition era; rematch before labeling')
            digest = history['evidence_hash']
            result = self.database.state(db,'document','autolabel-result:'+identifier) or {}
            key = 'autolabel-request:'+identifier
            current = self.database.state(db,'document',key) or {}
            hybrid = POLICY.startswith('siglip-rooms-openai-')
            same = current.get('evidence_hash')==digest and current.get('policy')==POLICY
            if full:
                if not hybrid: raise ValueError('Full batch requires the SigLIP hybrid room workflow')
                if same and (current.get('status')=='running' or current.get('status')=='queued' and current.get('mode')=='all' and current.get('label_provider')=='copilot'):
                    return {'status':current['status'],'stage':current.get('stage','rooms')}
                if same and current.get('status')=='completed' and current.get('mode')=='all' and current.get('label_provider')=='copilot':
                    return {'status':'completed'}
                valid_rooms = result.get('room_labels_complete') and result.get('evidence_hash')==digest and result.get('policy')==POLICY
                stage = 'features' if valid_rooms else 'rooms'
                self.database.save(db,'document',key,current.get('revision',0),{
                    'property_id':identifier,'evidence_hash':digest,'policy':POLICY,
                    'label_provider':'copilot','mode':'all','stage':stage,'status':'queued','at':now()})
                return {'status':'queued','stage':stage,'mode':'all'}
            if hybrid and same:
                if payload.get('retry') is True and current.get('status')=='failed' and current.get('stage','rooms')=='rooms':
                    self.database.save(db,'document',key,current.get('revision',0),{**current,'status':'queued','at':now()})
                    return {'status':'queued','stage':'rooms'}
                if payload.get('test') is True and current.get('status') in {'awaiting_test','completed','failed'}:
                    stage = 'features' if result.get('room_labels_complete') else 'rooms'
                    self.database.save(db,'document',key,current.get('revision',0),{
                        **current,'label_provider':label_provider,'stage':stage,'mode':'test','max_photos':8,'status':'queued','at':now()})
                    return {'status':'queued','stage':stage,'mode':'test'}
                return {'status':current['status'],'stage':current.get('stage','rooms'),'mode':current.get('mode','rooms')}
            if result.get('evidence_hash')==digest and result.get('policy')==POLICY:
                return {'status':'completed'}
            if current.get('evidence_hash')==digest and current.get('policy')==POLICY and current.get('status')=='failed' and not payload.get('retry'):
                return {'status':'failed'}
            if current.get('evidence_hash')==digest and current.get('policy')==POLICY and current.get('status') in {'queued','running'}:
                return {'status':current['status']}
            self.database.save(db,'document',key,current.get('revision',0),{
                'property_id':identifier,'evidence_hash':digest,'policy':POLICY,'label_provider':label_provider,
                'status':'queued','at':now(),**({'stage':'rooms','mode':'test' if payload.get('test') is True else 'rooms','max_photos':8} if hybrid else {})})
        return {'status':'queued'}

    def autolabel_pending(self, stage=None):
        from automatic_labels import active_policy
        with self.database.connect() as db:
            rows = db.execute('''SELECT payload FROM acq_training.studio_state
                WHERE workspace_id=%s AND kind='document' AND item_id LIKE 'autolabel-request:%%'
                AND payload->>'status' IN ('queued','running') AND payload->>'policy'=%s
                AND (%s::text IS NULL OR coalesce(payload->>'stage','rooms')=%s)
                ORDER BY updated_at LIMIT 50''',(self.workspace,active_policy(),stage,stage)).fetchall()
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
        if state_filter not in {'all','verified','verify','rematch','missing_photos','match_confirmed'}:
            raise ValueError('Unknown source-row status')
        with self.database.connect() as db:
            rows = db.execute('''SELECT e.id,e.listing_key,e.group_id,e.source_rows,e.source_snapshot,e.match_status,
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
        annotate_first_sales(rows)
        by_listing = {}
        for photo in photo_rows:
            by_listing.setdefault(photo['listing_key'],{}).setdefault(photo['provider_media_key'],set()).add(photo['image_sha256'])
        supported = {}
        for row in rows:
            supported[row['listing_key']] = supported.get(row['listing_key'],True) and supports_prior(self._selected(row),row['source_snapshot'].get('spreadsheet'),row['source_snapshot'].get('mls_candidates'))
        items = []
        for row in rows:
            selected = self._selected(row)
            source = trim_metadata(row['source_snapshot'].get('spreadsheet', {}))
            era = states.get(('era',row['listing_key']),{})
            validation = states.get(('document','mls-validation:'+str(row['id'])),{})
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
                items.append({'source_row':source_row,'listing_key':row['listing_key'],'import_match_status':row['match_status'],
                    'validation_record_id':str(row['id']),'validation_decision':validation.get('decision'),
                    'match_confirmed':row['match_status']=='confirmed' or validation.get('decision')=='confirmed',
                    'address':source.get('Address') or source.get('UnparsedAddress') or selected.get('listing',{}).get('UnparsedAddress') or 'Unresolved source row',
                    'source':source,'status':status,'photo_count':photo_count,
                    'photo_coverage':era.get('photo_coverage','unknown') if status=='verified' else 'unknown',
                    'verification_note':note,
                    'candidates':[{'listing':trim_metadata(c.get('listing',{})),
                                   'match':c.get('match',{}),'prior_supported':supports_prior(c,row['source_snapshot'].get('spreadsheet'),row['source_snapshot'].get('mls_candidates'))}
                                  for c in row['source_snapshot'].get('mls_candidates',[])]})
        # Source rows identify workbook records; do not collapse repeated parcels.
        unique = {item['source_row']:item for item in items}
        items = sorted(unique.values(),key=lambda item:item['source_row'])
        counts = {state:sum(i['status']==state for i in items) for state in ('verified','verify','rematch','missing_photos')}
        counts['match_confirmed'] = sum(i['match_confirmed'] for i in items)
        total_rows = len(items)
        items = [i for i in items if (state_filter=='all' or i['status']==state_filter or state_filter=='match_confirmed' and i['match_confirmed'])
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

    def _validation_example(self, db, identifier):
        try: example_id = UUID(str(identifier))
        except ValueError: raise ValueError('Unknown validation record') from None
        row = db.execute('''SELECT e.id,e.listing_key,e.listing_id,e.match_status,e.source_rows,
            e.target_transaction,e.source_snapshot,e.group_id
            FROM acq_training.examples e WHERE e.workspace_id=%s AND e.id=%s
            AND e.match_status IN ('candidate','unresolved')''',(self.workspace,example_id)).fetchone()
        if not row: raise ValueError('Unknown validation record')
        return row

    @staticmethod
    def _validation_summary(example, review=None):
        source = example.get('source_snapshot',{}).get('spreadsheet',{})
        candidate = validation_candidate(example)
        listing = candidate.get('listing',{})
        return {
            'id':str(example['id']),'match_status':example['match_status'],
            'source_rows':example.get('source_rows',[]),
            'source_address':source.get('Address') or source.get('UnparsedAddress') or 'Address unavailable',
            'apn':source.get('APN') or source.get('ParcelNumber') or 'APN unavailable',
            'prior_sale_date':source.get('Prior Sale Date') or example.get('target_transaction',{}).get('Prior Sale Date'),
            'prior_sale_amount':source.get('Prior Sale Amount') or example.get('target_transaction',{}).get('Prior Sale Amount'),
            'last_sale_date':source.get('Last Sale Date') or example.get('target_transaction',{}).get('Last Sale Date'),
            'last_sale_amount':source.get('Last Sale Amount') or example.get('target_transaction',{}).get('Last Sale Amount'),
            'listing_key':str(listing.get('ListingKey') or ''),
            'listing_id':listing.get('ListingId'),
            'listing_address':listing.get('UnparsedAddress'),
            'close_date':listing.get('CloseDate'),'close_price':listing.get('ClosePrice'),
            'decision':(review or {}).get('decision'),'revision':(review or {}).get('revision',0),
        }

    def mls_validation_queue(self, args):
        state_filter = args.get('status','pending')
        if state_filter not in {'pending','reviewed','all'}: raise ValueError('Unknown validation queue')
        offset,limit = max(0,int(args.get('offset',0))),min(40,max(1,int(args.get('limit',20))))
        search = args.get('search','').strip().casefold()
        with self.database.connect() as db:
            rows = db.execute('''SELECT e.id,e.listing_key,e.listing_id,e.match_status,e.source_rows,
                e.target_transaction,e.source_snapshot,e.group_id
                FROM acq_training.examples e WHERE e.workspace_id=%s
                AND e.match_status IN ('candidate','unresolved')
                ORDER BY CASE e.match_status WHEN 'candidate' THEN 0 ELSE 1 END,e.id''',
                (self.workspace,)).fetchall()
            states = {
                row['item_id']:{**row['payload'],'revision':row['revision']}
                for row in db.execute('''SELECT item_id,payload,revision FROM acq_training.studio_state
                    WHERE workspace_id=%s AND kind='document'
                    AND item_id LIKE 'mls-validation:%%' ''',(self.workspace,)).fetchall()
            }
        items = [
            self._validation_summary(row,states.get('mls-validation:'+str(row['id'])))
            for row in rows
        ]
        reviewed = sum(bool(item['decision']) for item in items)
        counts = {
            'total':len(items),'reviewed':reviewed,'remaining':len(items)-reviewed,
            'candidate':sum(item['match_status']=='candidate' for item in items),
            'unresolved':sum(item['match_status']=='unresolved' for item in items),
        }
        items = [item for item in items
                 if (state_filter=='all' or (state_filter=='reviewed')==bool(item['decision']))
                 and (not search or search in ' '.join(str(item.get(key) or '')
                     for key in ('source_address','apn','listing_id','listing_address')).casefold())]
        return {'items':items[offset:offset+limit],'total':len(items),'offset':offset,'limit':limit,
                'counts':counts}

    def mls_validation_detail(self, identifier):
        with self.database.connect() as db:
            example = self._validation_example(db,identifier)
            review = self.database.state(db,'document','mls-validation:'+str(example['id']))
            validation_media = self.database.state(
                db,'document','mls-validation-media:'+str(example['id'])
            ) or {}
            validation_photos = validation_media.get('images',[])
        result = self._validation_summary(example,review)
        candidate = validation_candidate(example)
        listing = candidate.get('listing',{})
        result['listing_remarks'] = __import__('listing_text').remarks(listing)
        result['listing_metadata'] = trim_metadata(listing)
        result['match_evidence'] = {
            key:candidate.get('match',{}).get(key) for key in
            ('exact_apn','street_number_matches','unit_conflict','rank_score',
             'transaction_match','best_sale_context')
        }
        result['photos'] = []
        if result['listing_key']:
            try:
                detail = self.property(result['listing_key'])
                result['photos'] = [{
                    'id':image['id'],'room':image.get('effective',{}).get('room','interior'),
                    'sequence':image.get('sequence'),
                } for image in validation_photo_gallery(detail)]
            except (ValueError,OSError):
                pass
        if not result['photos'] and validation_photos:
            rows = [{
                'id':'validation:'+str(example['id'])+':'+str(row['provider_media_key']),
                'room':validation_room(row['context_evidence'].get('provider_metadata',{})),
                'sequence':row['context_evidence'].get('provider_metadata',{}).get('Order'),
            } for row in validation_photos]
            preferred = []
            for room in ('kitchen','bathroom'):
                match = next((row for row in rows if row['room']==room and row not in preferred),None)
                if match: preferred.append(match)
            for row in rows:
                if len(preferred)>=8: break
                if row not in preferred: preferred.append(row)
            result['photos'] = preferred[:8]
        return result

    def save_mls_validation(self, payload):
        decision = payload.get('decision')
        if decision not in {'confirmed','wrong_listing','wrong_era','unsure'}:
            raise ValueError('Choose a validation decision')
        reviewer = payload.get('reviewer')
        if not isinstance(reviewer,str) or not 1<=len(reviewer.strip())<=100:
            raise ValueError('Reviewer name required')
        with self.database.connect() as db:
            example = self._validation_example(db,payload.get('id'))
            key = 'mls-validation:'+str(example['id'])
            self._lock_property(db,key)
            current = self.database.state(db,'document',key)
            candidate = validation_candidate(example)
            listing = candidate.get('listing',{})
            record = {
                'example_id':str(example['id']),'decision':decision,'reviewer':reviewer.strip(),
                'reviewed_at':now(),'source_rows':example.get('source_rows',[]),
                'original_match_status':example['match_status'],
                'selected_listing_key':str(listing.get('ListingKey') or ''),
                'selected_listing_id':listing.get('ListingId'),
                'certified_for_training':decision=='confirmed',
                'policy':'human-acquisition-mls-validation-v1',
            }
            result = self.database.save(
                db,'document',key,payload.get('expected_revision'),record
            )
        return result

    def apply_proposals(self, output):
        # Editable machine suggestions never overwrite reviewed labels.
        for proposal in output:
            key = 'model-proposal:'+proposal['property_id']
            current = self.document(key)
            self.save_document(key, proposal, (current or {}).get('revision', 0))
