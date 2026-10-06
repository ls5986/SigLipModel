"""Explicit local model worker using frozen Supabase reviews, never a local label fallback."""
import shutil
from uuid import uuid4

from acquisition_policy import supports_prior, annotate_first_sales
from model_loop import fingerprint
from pilot import ROOT, UnionFind, read_json, write_json
from property_models import ACCEPTED_METADATA_KEYS, metadata_features
from studio_data import now
from studio_jobs import StudioJobs

COHORT_KEY = 'training-cohort-acquisition-250-v1'


def readiness(properties):
    eligible = [p for p in properties if p.get('known_target') and p['training_allowed'] and not p.get('label_exclusion')]
    heldout = [p for p in properties if p.get('known_target') and p['split']=='test' and not p.get('label_exclusion')]
    training_groups = len({p['group_id'] for p in eligible})
    evaluation_groups = len({p['group_id'] for p in heldout})
    reasons = []
    if training_groups < 5: reasons.append('Verify acquisition sale and photos for at least five independent training groups')
    if evaluation_groups < 2: reasons.append('Verify acquisition sale and photos for at least two protected evaluation groups')
    return {'cohort':'all-imported-known-targets-v2','objective':'known-target-similarity-v1',
            'properties':len(properties), 'reviewed':sum(p['timing_verified'] for p in properties),
            'pending_property_reviews':0, 'pending_photo_matches':sum(not p['timing_verified'] for p in properties),
            'eligible_targets':len(eligible), 'eligible_not_targets':0,
            'independent_groups':{'training':training_groups,'evaluation':evaluation_groups},
            'protected_properties':sum(p['split']=='test' for p in properties),
            'verified_heldout_properties':len(heldout),'uncertain':0,
            'ready':not reasons, 'reasons':reasons,
            'notice':'Known targets from workbook provenance. Overall target/pass ratings are not required. Pending rows are excluded.'}


def snapshot(store, *, include_legacy=True):
    """One repeatable DB snapshot; protect duplicate/physical groups before filtering the cohort."""
    with store.database.connect() as db:
        db.execute("SET LOCAL statement_timeout='60000ms'")
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        cohort = store.database.state(db, 'document', COHORT_KEY)
        cohort = cohort or {'listing_keys':[]}
        keys = cohort['listing_keys']
        validation_rows = db.execute('''SELECT item_id,payload FROM acq_training.studio_state
            WHERE workspace_id=%s AND kind='document' AND item_id LIKE 'mls-validation:%%'
            AND payload->>'decision'='confirmed' AND payload->>'certified_for_training'='true' ''',
            (store.workspace,)).fetchall()
        validations = {
            row['item_id'].split(':',1)[1]:row['payload'] for row in validation_rows
        }
        validation_media_rows = db.execute('''SELECT item_id,payload
            FROM acq_training.studio_state WHERE workspace_id=%s AND kind='document'
            AND item_id LIKE 'mls-validation-media:%%' ''',(store.workspace,)).fetchall()
        validation_media = {
            row['item_id'].split(':',1)[1]:row['payload'] for row in validation_media_rows
        }
        records = db.execute('''SELECT e.id,e.listing_key,e.group_id,e.source_rows,
                jsonb_build_object('spreadsheet',jsonb_build_object('Prior Sale Date',e.source_snapshot->'spreadsheet'->>'Prior Sale Date','Last Sale Date',e.source_snapshot->'spreadsheet'->>'Last Sale Date'),'mls_candidates',jsonb_build_array(jsonb_build_object(
                    'listing', c.item->'listing', 'match', c.item->'match'))) AS source_snapshot,
                g.identity_key,g.identity_verified,g.protected_test
            FROM acq_training.examples e JOIN acq_training.property_groups g
            ON (g.workspace_id,g.id)=(e.workspace_id,e.group_id)
            LEFT JOIN LATERAL (SELECT item FROM jsonb_array_elements(e.source_snapshot->'mls_candidates') item
              WHERE item->'listing'->>'ListingKey'=e.listing_key LIMIT 1) c ON true
            WHERE e.workspace_id=%s AND e.listing_key IS NOT NULL AND cardinality(e.source_rows)>0 ORDER BY e.id''', (store.workspace,)).fetchall()
        if validations:
            extra = db.execute('''SELECT e.id,e.group_id,e.source_rows,e.source_snapshot,
                  g.identity_key,g.identity_verified,g.protected_test
              FROM acq_training.examples e JOIN acq_training.property_groups g
              ON (g.workspace_id,g.id)=(e.workspace_id,e.group_id)
              WHERE e.workspace_id=%s AND e.id=ANY(%s::uuid[])''',
              (store.workspace,list(validations))).fetchall()
            existing = {str(row['id']) for row in records}
            for row in extra:
              if str(row['id']) in existing: continue
              validation = validations[str(row['id'])]
              listing_key = validation.get('selected_listing_key')
              selected = next((candidate for candidate in row['source_snapshot'].get('mls_candidates',[])
                  if str(candidate.get('listing',{}).get('ListingKey'))==str(listing_key)),None)
              if not selected: continue
              records.append({
                  **row,'listing_key':str(listing_key),
                  'source_snapshot':{
                      'spreadsheet':row['source_snapshot'].get('spreadsheet',{}),
                      'mls_candidates':[selected],
                  },
              })
        photos = db.execute('''SELECT p.*,e.id AS example_id,e.listing_key,e.group_id
            FROM acq_training.photos p
            JOIN acq_training.examples e ON (e.workspace_id,e.id)=(p.workspace_id,p.example_id)
            WHERE p.workspace_id=%s AND p.revoked_at IS NULL
            AND (p.retention_until IS NULL OR p.retention_until>now()) ORDER BY p.id''',
            (store.workspace,)).fetchall()
        photos = [dict(photo) for photo in photos]
        existing_photo_examples = {str(photo['example_id']) for photo in photos}
        existing_photo_listings = {
            str(photo['listing_key']) for photo in photos if photo.get('listing_key')
        }
        for photo in photos:
            validation = validations.get(str(photo['example_id']))
            if validation and not photo.get('listing_key'):
                photo['listing_key'] = validation.get('selected_listing_key')
        records_by_id = {str(record['id']):record for record in records}
        for example_id,media in validation_media.items():
            validation = validations.get(example_id)
            record = records_by_id.get(example_id)
            if (not validation or not record or example_id in existing_photo_examples
                    or str(validation.get('selected_listing_key')) in existing_photo_listings):
                continue
            for image in media.get('images',[]):
                photos.append({
                    **image,'example_id':record['id'],
                    'listing_key':validation.get('selected_listing_key'),
                    'group_id':record['group_id'],'revoked_at':None,
                    'retention_until':None,
                })
        keys = sorted({r['listing_key'] for r in records})
        legacy = store._legacy(db, [*keys, *[r['listing_key']+':'+str(r['provider_media_key'])
                                               for r in photos if r['listing_key'] in keys]]) if include_legacy else {}
        live = store._reviews(db, [*keys, *['autolabel-result:'+k for k in keys], *[r['listing_key']+':'+str(r['provider_media_key'])
                                          for r in photos if r['listing_key'] in keys]])
        # Imported base schema did not restore protected_test flags. Recover original test
        # identities from the immutable manifest before joining physical/image aliases.
        held = db.execute('''SELECT i->>'listing_key' AS listing_key,i->>'sha256' AS sha256
            FROM acq_training.migration_documents d,
              jsonb_array_elements(d.payload->'images') i
            WHERE d.workspace_id=%s AND replace(d.logical_path,chr(92),'/')='data/manifest.json'
              AND i->>'split'='test' ''', (store.workspace,)).fetchall()
    annotate_first_sales(records)
    union = UnionFind(sorted({str(r['group_id']) for r in records}))
    by_listing, by_identity, by_hash = {}, {}, {}
    for r in records:
        group = str(r['group_id'])
        for identity, index in ((r['listing_key'], by_listing),
                                (r['identity_key'] if r['identity_verified'] else None, by_identity)):
            if identity:
                if identity in index: union.union(group, index[identity])
                index[identity] = group
    for p in photos:
        group, digest = str(p['group_id']), p['image_sha256']
        if digest in by_hash: union.union(group, by_hash[digest])
        by_hash[digest] = group
    held_keys = {r['listing_key'] for r in held}
    held_hashes = {r['sha256'] for r in held}
    protected = {union.find(str(r['group_id'])) for r in records
                 if r['protected_test'] or r['listing_key'] in held_keys}
    protected.update(union.find(str(p['group_id'])) for p in photos if p['image_sha256'] in held_hashes)
    # Reserve whole linked groups for this new cohort, including original test aliases.
    # Membership is reproducible and does not depend on observed target answers.
    import hashlib
    cohort_groups = {union.find(str(r['group_id'])) for r in records if r['listing_key'] in keys}
    reserved = sorted(cohort_groups, key=lambda g: hashlib.sha256(('acquisition-250-v1:'+g).encode()).hexdigest())
    # Stable hash membership survives later recovery of additional workbook rows.
    protected.update(g for g in reserved if int(hashlib.sha256(('known-target-holdout-v2:'+g).encode()).hexdigest()[:8],16) / 2**32 < .2)
    original_groups = {union.find(str(r['group_id'])) for r in records if r['listing_key'] in cohort['listing_keys']}
    original_reserved = sorted(original_groups,key=lambda g:hashlib.sha256(('acquisition-250-v1:'+g).encode()).hexdigest())
    protected.update(original_reserved[:max(1,len(original_reserved)//5)])
    examples, properties = [], []
    for key in keys:
        sources = [r for r in records if r['listing_key'] == key]
        if not sources: raise ValueError('Frozen cohort listing is missing')
        attached = [r for r in photos if r['listing_key'] == key]
        unique = {}
        for p in attached:
            identifier = key+':'+str(p['provider_media_key'])
            if identifier in unique and unique[identifier]['image_sha256'] != p['image_sha256']:
                raise ValueError('Conflicting photo identities must be reconciled')
            unique[identifier] = p
        group = union.find(str(sources[0]['group_id']))
        split = 'test' if group in protected else 'train'
        photo_rows = [dict(p, image_id=i) for i,p in unique.items()]
        era = live.get(('era', key))
        # _history expects source_rows as well as source_snapshot.
        historical_sources = sources
        history = store._history(historical_sources, photo_rows, era)
        manually_certified = any(
            validations.get(str(source['id']),{}).get('certified_for_training') is True
            for source in sources
        )
        media_complete = True
        if manually_certified:
            validations_for_sources = [
                (source,validations.get(str(source['id']),{}),
                 validation_media.get(str(source['id']),{}))
                for source in sources if str(source['id']) in validations
            ]
            media_complete = all(
                int(store._selected(source).get('listing',{}).get('PhotosCount') or 0)==0
                or str(source['id']) in existing_photo_examples
                or str(validation.get('selected_listing_key')) in existing_photo_listings
                or media.get('status') in {'complete','sampled'}
                and int(media.get('provider_media_count') or 0)>0
                and len(media.get('images',[]))==int(media.get('provider_media_count') or 0)
                for source,validation,media in validations_for_sources
            )
        verified = manually_certified and media_complete or (
            history['timing_verified'] and all(
                supports_prior(store._selected(r),r['source_snapshot'].get('spreadsheet'),
                               r['source_snapshot'].get('mls_candidates'))
                for r in sources
            )
        )
        metadata = store._selected(sources[0]).get('listing', {})
        review = store._review('property', key, legacy, live)
        properties.append({'id':key, 'example_id':str(sources[0]['id']),
            'source_group_id':str(sources[0]['group_id']),
            'physical_key':group, 'group_id':group, 'split':split,
            'metadata': {k:v for k,v in metadata.items() if k in ACCEPTED_METADATA_KEYS},
            'model_metadata':metadata_features(metadata), 'review':review,
            'mls_remarks':__import__('listing_text').remarks(metadata),
            'synthetic_evidence':__import__('listing_text').image_evidence(metadata),
            'human_review_revision':review.get('revision',0), 'timing_verified':verified,
            'text_source_valid':not history['blocked'],
            'photo_coverage':'no_interior' if manually_certified and not photo_rows else history['photo_coverage'],
            'known_target':verified, 'target_origin':'human-certified-mls-validation'
                if manually_certified else 'user-confirmed-workbook-cohort',
            'source_rows':sorted({n for r in sources for n in r['source_rows']}),
            'training_allowed':verified and split != 'test',
            'label_exclusion':None if verified else
                'Certified MLS media sample is incomplete' if manually_certified else
                'Acquisition era is not verified'})
        for identifier,p in unique.items():
            review = store._review('image', identifier, legacy, live)
            approved = review.get('status') == 'approved'
            context = review.get('context') if approved else p.get('context')
            provider = p.get('context_evidence',{}).get('provider_metadata',{})
            description = str(provider.get('LongDescription') or provider.get('ShortDescription') or '').casefold()
            if context in {None,'unknown'}:
                if any(term in description for term in ('community pool','community room','community exercise','hoa','clubhouse')): context='shared_amenity'
                elif 'floor plan' in description or 'floorplan' in description: context='floor_plan'
            automatic = live.get(('document','autolabel-result:'+key),{})
            from automatic_labels import active_policy
            if automatic.get('policy') != active_policy() or automatic.get('evidence_hash') != history['evidence_hash']: automatic = {}
            draft = next((i for i in automatic.get('images',[]) if i.get('image_id')==identifier and i.get('sha256')==p['image_sha256']),{})
            if context in {None,'unknown'}: context = draft.get('context',context)
            from photo_selection import selection
            from listing_text import image_evidence
            disclosure = image_evidence(metadata,description)
            included = selection(context,review,disclosure)['included']
            excluded = not verified or not included
            condition_excluded = not verified or disclosure['excluded'] or context in {'shared_amenity','floor_plan','unrelated'}
            examples.append({'id':identifier, 'property_id':key, 'group_id':group, 'split':split,
                'physical_key':group, 'sha256':p['image_sha256'],
                'path':None,
                'storage_bucket':p['storage_bucket'], 'storage_object_key':p['storage_object_key'],
                'room':review.get('room') if approved and not condition_excluded else None,
                'proposed_room':review.get('room') or 'other',
                'features':review.get('features',{}) if approved and not condition_excluded else {},
                'condition_label':review.get('condition_label','unknown') if approved and not condition_excluded and history['photo_coverage']!='no_interior' else 'unknown',
                'preference':review.get('preference') if approved and not condition_excluded else None,
                'preference_room':review.get('preference_room') or review.get('room') or 'other',
                'photo_context':context, 'human_review_revision':review.get('revision',0),
                'include_in_similarity':included,
                'label_exclusion':'Unverified era or excluded photo' if excluded else None})
    return examples, properties


class SupabaseJobs(StudioJobs):
    """Opt-in laptop worker; hosted review server never instantiates this class."""
    def __init__(self, store, root=ROOT):
        super().__init__(store, root)

    def _snapshot(self):
        rows, _ = snapshot(self.store)
        return rows, 0

    def _property_snapshot(self):
        return snapshot(self.store)[1]

    def preview(self, kind='train'):
        if kind != 'train': raise ValueError('Cloud worker only supports reviewed training')
        rows, properties = snapshot(self.store)
        gate = readiness(properties)
        if not gate['ready']: raise ValueError('; '.join(gate['reasons']))
        required = ['artifacts/backbone.json','artifacts/silver_heads.joblib','data/manifest.json','data/embeddings.npz']
        if any(not (self.root/p).is_file() for p in required):
            raise ValueError('Restore the existing local backbone and embedding artifacts before training')
        identifier = uuid4().hex
        folder = self.folder/identifier
        folder.mkdir()
        evidence = self.root/'cloud-evidence'
        evidence.mkdir(exist_ok=True)
        for row in rows:
            if row.get('label_exclusion'): continue
            path = evidence/row['sha256']
            if not path.exists():
                cached = self.store.storage.get(row['storage_bucket'],row['storage_object_key'],row['sha256'])
                shutil.copyfile(cached,path)
            row['path'] = str(path)
        preview = {**gate, 'id':identifier, 'kind':kind, 'eligible_images':sum(not r.get('label_exclusion') for r in rows)}
        frozen = {'kind':kind,'examples':rows,'properties':properties,'created_at':now(),
                  'review_fingerprint':fingerprint(rows,properties),'preview':preview,
                  'objective':'known-target-similarity-v1'}
        write_json(folder/'snapshot.json',frozen)
        write_json(folder/'status.json',{'id':identifier,'kind':kind,'status':'preview',
                                        'created_at':now(),'detail':preview})
        return preview

    def start(self, payload, kind='train'):
        identifier = payload.get('id','')
        if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
            raise ValueError('Unknown job preview')
        file = self.folder/identifier/'snapshot.json'
        if not file.is_file(): raise ValueError('Create a training preview before starting')
        frozen = read_json(file)
        rows, properties = snapshot(self.store)
        if not readiness(properties)['ready'] or fingerprint(rows,properties) != frozen['review_fingerprint']:
            raise ValueError('Reviews or eligibility changed; create a new preview')
        return super().start(payload,kind)

    def _run(self, folder):
        super()._run(folder)
        current = self.store.document('training-worker-latest')
        state = read_json(folder/'status.json')
        summary = {key:state.get(key) for key in ('id','kind','status','created_at','started_at','finished_at','error')}
        if state['status']=='completed':
            pointer = read_json(self.root/'artifacts'/'studio_candidate_latest.json')
            summary.update(version=pointer['version'], heads_sha256=pointer['heads_sha256'],
                           review_fingerprint=pointer['review_fingerprint'], metrics=pointer['metrics'])
        self.store.save_document('training-worker-latest',summary,(current or {}).get('revision',0))
