"""Explicit local model worker using frozen Supabase reviews, never a local label fallback."""
from collections import Counter
import shutil
from uuid import uuid4

from acquisition_policy import supports_prior
from model_loop import fingerprint
from pilot import ROOT, UnionFind, read_json, write_json
from property_models import ACCEPTED_METADATA_KEYS, metadata_features, target_class
from studio_data import now
from studio_jobs import StudioJobs

COHORT_KEY = 'training-cohort-acquisition-250-v1'


def readiness(properties):
    missing = [r['id'] for r in properties if r['review'].get('status') != 'approved']
    era = [r['id'] for r in properties if not r['timing_verified']]
    eligible = [r for r in properties if r['training_allowed'] and target_class(r['review']) is not None]
    group_labels = {}
    for row in eligible: group_labels.setdefault(row['group_id'], set()).add(target_class(row['review']))
    conflicts = {group for group, values in group_labels.items() if len(values)>1}
    counts = Counter(target_class(r['review']) for r in eligible)
    groups = {label: len({r['group_id'] for r in eligible if target_class(r['review']) == label})
              for label in (0, 1)}
    reasons = []
    if conflicts: reasons.append(f'{len(conflicts)} physical groups have conflicting property target answers')
    if missing: reasons.append(f'{len(missing)} properties still need an overall target rating')
    if era: reasons.append(f'{len(era)} photo sets still need acquisition-era verification')
    if min(counts.get(0, 0), counts.get(1, 0)) < 5 or min(groups.values()) < 3:
        reasons.append('Need five decisive ratings and three independent groups in each target class')
    return {'cohort': 'acquisition-250-v1', 'properties': len(properties),
            'reviewed': len(properties)-len(missing), 'pending_property_reviews': len(missing),
            'pending_photo_matches': len(era), 'eligible_targets': counts.get(1, 0),
            'eligible_not_targets': counts.get(0, 0), 'independent_groups': groups,
            'uncertain': sum(r['review'].get('status') == 'approved' and target_class(r['review']) is None
                             for r in properties),
            'protected_properties': sum(r['split'] == 'test' for r in properties),
            'ready': not reasons and bool(properties), 'reasons': reasons}


def snapshot(store):
    """One repeatable DB snapshot; protect duplicate/physical groups before filtering the cohort."""
    with store.database.connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        cohort = store.database.state(db, 'document', COHORT_KEY)
        if not cohort or len(cohort.get('listing_keys', [])) != 250:
            raise ValueError('Freeze the 250-property training cohort first')
        keys = cohort['listing_keys']
        records = db.execute('''SELECT e.id,e.listing_key,e.group_id,e.source_rows,
                jsonb_build_object('mls_candidates',jsonb_build_array(jsonb_build_object(
                    'listing', c.item->'listing', 'match', c.item->'match'))) AS source_snapshot,
                g.identity_key,g.identity_verified,g.protected_test
            FROM acq_training.examples e JOIN acq_training.property_groups g
            ON (g.workspace_id,g.id)=(e.workspace_id,e.group_id)
            LEFT JOIN LATERAL (SELECT item FROM jsonb_array_elements(e.source_snapshot->'mls_candidates') item
              WHERE item->'listing'->>'ListingKey'=e.listing_key LIMIT 1) c ON true
            WHERE e.workspace_id=%s ORDER BY e.id''', (store.workspace,)).fetchall()
        photos = db.execute('''SELECT p.*,e.listing_key,e.group_id FROM acq_training.photos p
            JOIN acq_training.examples e ON (e.workspace_id,e.id)=(p.workspace_id,p.example_id)
            WHERE p.workspace_id=%s AND p.revoked_at IS NULL
            AND (p.retention_until IS NULL OR p.retention_until>now()) ORDER BY p.id''',
            (store.workspace,)).fetchall()
        legacy = store._legacy(db, [*keys, *[r['listing_key']+':'+str(r['provider_media_key'])
                                               for r in photos if r['listing_key'] in keys]])
        live = store._reviews(db, [*keys, *[r['listing_key']+':'+str(r['provider_media_key'])
                                          for r in photos if r['listing_key'] in keys]])
        # Imported base schema did not restore protected_test flags. Recover original test
        # identities from the immutable manifest before joining physical/image aliases.
        held = db.execute('''SELECT i->>'listing_key' AS listing_key,i->>'sha256' AS sha256
            FROM acq_training.migration_documents d,
              jsonb_array_elements(d.payload->'images') i
            WHERE d.workspace_id=%s AND replace(d.logical_path,chr(92),'/')='data/manifest.json'
              AND i->>'split'='test' ''', (store.workspace,)).fetchall()
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
    protected.update(reserved[:max(1, len(reserved)//5)])
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
        verified = history['timing_verified'] and all(supports_prior(store._selected(r)) for r in sources)
        metadata = store._selected(sources[0]).get('listing', {})
        review = store._review('property', key, legacy, live)
        properties.append({'id':key, 'physical_key':group, 'group_id':group, 'split':split,
            'metadata': {k:v for k,v in metadata.items() if k in ACCEPTED_METADATA_KEYS},
            'model_metadata':metadata_features(metadata), 'review':review,
            'human_review_revision':review.get('revision',0), 'timing_verified':verified,
            'training_allowed':verified and split != 'test',
            'label_exclusion':None if verified else 'Acquisition era is not verified'})
        for identifier,p in unique.items():
            review = store._review('image', identifier, legacy, live)
            approved = review.get('status') == 'approved'
            context = review.get('context') if approved else None
            excluded = not verified or context in {'shared_amenity','floor_plan','unrelated'}
            examples.append({'id':identifier, 'property_id':key, 'group_id':group, 'split':split,
                'physical_key':group, 'sha256':p['image_sha256'],
                'path':None,
                'storage_bucket':p['storage_bucket'], 'storage_object_key':p['storage_object_key'],
                'room':review.get('room') if approved and not excluded else None,
                'proposed_room':review.get('room') or 'other',
                'features':review.get('features',{}) if approved and not excluded else {},
                'preference':review.get('preference') if approved and not excluded else None,
                'preference_room':review.get('preference_room') or review.get('room') or 'other',
                'photo_context':context, 'human_review_revision':review.get('revision',0),
                'label_exclusion':'Unverified era or non-subject photo' if excluded else None})
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
            path = evidence/row['sha256']
            if not path.exists():
                cached = self.store.storage.get(row['storage_bucket'],row['storage_object_key'],row['sha256'])
                shutil.copyfile(cached,path)
            row['path'] = str(path)
        preview = {**gate, 'id':identifier, 'kind':kind, 'eligible_images':len(rows)}
        frozen = {'kind':kind,'examples':rows,'properties':properties,'created_at':now(),
                  'review_fingerprint':fingerprint(rows,properties),'preview':preview}
        write_json(folder/'snapshot.json',frozen)
        write_json(folder/'status.json',{'id':identifier,'kind':kind,'status':'preview',
                                        'created_at':now(),'detail':preview})
        return preview

    def start(self, payload, kind='train'):
        identifier = payload.get('id','')
        if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
            raise ValueError('Unknown job preview')
        frozen = read_json(self.folder/identifier/'snapshot.json')
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
