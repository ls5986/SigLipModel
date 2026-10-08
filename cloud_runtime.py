"""Cloud-data review runtime. UI remains loopback-only until hosted auth is added."""
import hashlib
import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cloud_storage import PrivateStorage
from cloud_store import Database
from validated_listing_store import ValidatedListingStore as SupabaseStore
from config import DATA_ROOT


class CloudStudio:
    def __init__(self, app, store, model_worker=False):
        self.app, self.store = app, store
        self.jobs = self.connection = self.exchange = None
        self._mls_source = None
        if model_worker:
            from cloud_training import SupabaseJobs
            from acq_exchange import Exchange
            from connected_worker import ConnectedWorker
            self.jobs = SupabaseJobs(store)
            self.exchange = Exchange(self.jobs)
            self.connection = ConnectedWorker(self.exchange)

    def mls_source(self):
        if self._mls_source is None:
            from mls_source import ExistingMLSSupabaseSource
            self._mls_source = ExistingMLSSupabaseSource.from_env()
        return self._mls_source

    def challenge_image(self, listing_key, media_key, batch_id=None):
        blob = self.mls_source().image_bytes(listing_key,media_key)
        if batch_id:
            from model_workbench import challenge_item
            item = challenge_item(
                self.store,f"challenge:{batch_id}:{listing_key}",
            )
            photo = next((
                photo for photo in item["images"]
                if photo["media_key"]==str(media_key)
            ),None)
            if not photo or hashlib.sha256(blob).hexdigest()!=photo.get("sha256"):
                raise ValueError("MLS photo changed after this challenge batch was frozen")
        return blob

    def get(self, path):
        parsed = urlparse(path)
        args = {k:v[0] for k,v in parse_qs(parsed.query).items()}
        if parsed.path.startswith('/api/studio/v2/'):
            from studio_v2 import get
            return get(self, path)
        if parsed.path.startswith('/api/studio/workbench'):
            from model_workbench import (
                candidate_history,challenge_batch,challenge_batches,challenge_item,
                challenge_properties,challenge_property,compare_candidates,error_queue,
                list_properties,property_detail,run_status,summary,
            )
            if parsed.path=='/api/studio/workbench/summary':
                return {**summary(self.store),'token':self.app.token}
            if parsed.path=='/api/studio/workbench/properties':
                return {**list_properties(self.store,args),'token':self.app.token}
            if parsed.path=='/api/studio/workbench/property':
                return property_detail(self.store,args.get('id'))
            if parsed.path=='/api/studio/workbench/run':
                return run_status(self.store,args.get('id'))
            if parsed.path=='/api/studio/workbench/errors':
                return error_queue(self.store,args)
            if parsed.path=='/api/studio/workbench/challenge/properties':
                return {**challenge_properties(self.store,self.mls_source(),args),
                        'token':self.app.token}
            if parsed.path=='/api/studio/workbench/challenge/property':
                return challenge_property(self.mls_source(),args.get('id'))
            if parsed.path=='/api/studio/workbench/challenge/batches':
                return challenge_batches(self.store)
            if parsed.path=='/api/studio/workbench/challenge/batch':
                return challenge_batch(self.store,args.get('id'))
            if parsed.path=='/api/studio/workbench/challenge/fixed-property':
                return challenge_item(self.store,args.get('id'))
            if parsed.path=='/api/studio/workbench/candidates':
                return candidate_history(self.store)
            if parsed.path=='/api/studio/workbench/compare':
                return compare_candidates(self.store,args)
        if parsed.path=='/api/studio/source-rows': return self.store.source_rows(args)
        if parsed.path=='/api/studio/mls-validation-queue':
            return {**self.store.mls_validation_queue(args),'token':self.app.token}
        if parsed.path=='/api/studio/mls-validation': return self.store.mls_validation_detail(args.get('id'))
        if parsed.path=='/api/studio/autolabel': return self.store.autolabel_status(args.get('id',''))
        if parsed.path=='/api/studio/training-readiness':
            return self.store.training_readiness()
        if self.jobs:
            if parsed.path=='/api/studio/connection': return self.connection.public_status()
            if parsed.path=='/api/studio/model-loop':
                from model_loop import status
                return status(self.jobs)
            if parsed.path=='/api/studio/acq-comparison': return self.exchange.result(args.get('id'))
        if parsed.path=='/api/studio/review-queue':
            return {**self.store.queue(args),'token':self.app.token}
        if parsed.path=='/api/studio/property':
            return self.store.property(args.get('id'))
        if parsed.path=='/api/studio/summary':
            queue=self.store.queue({'scope':'acquisitions','queue':'all','offset':0,'limit':1})
            return {'token':self.app.token,'storage':'supabase',
                    'capabilities':{'review':True,'training':self.jobs is not None,'assessment':False},
                    'jobs':self.jobs.list_jobs() if self.jobs else [],
                    'counts':queue['counts'],'photo_count':queue['photo_count'],
                    'metadata_policy':{'stored':'sanitized confirmed-listing snapshot',
                                       'model_feature_groups':9,'post_decision_outcomes':False},
                    'notice':'Reviews and photos use Supabase. Model jobs need the separate hosted worker cutover.'}
        raise ValueError('This action is not available in cloud review mode yet')

    def post(self, path, payload):
        from studio_v2 import TRAINING_ACTIONS, require_operator
        if path in TRAINING_ACTIONS and self.jobs is not None:
            require_operator()
        if path.startswith('/api/studio/v2/'):
            from studio_v2 import post
            return post(self, path, payload)
        if path.startswith('/api/studio/workbench'):
            if path in TRAINING_ACTIONS:
                require_operator()
            from model_workbench import (
                dataset_preview,freeze_challenge_batch,freeze_dataset,queue_run,
                queue_training,save_feedback,
            )
            if path=='/api/studio/workbench/run': return queue_run(self.store,payload)
            if path=='/api/studio/workbench/feedback': return save_feedback(self.store,payload)
            if path=='/api/studio/workbench/dataset/preview': return dataset_preview(self.store)
            if path=='/api/studio/workbench/dataset/freeze': return freeze_dataset(self.store,payload)
            if path=='/api/studio/workbench/train': return queue_training(self.store,payload)
            if path=='/api/studio/workbench/challenge/batch':
                return freeze_challenge_batch(self.store,self.mls_source(),payload)
        if path=='/api/studio/mls-validation': return self.store.save_mls_validation(payload)
        if path=='/api/studio/autolabel': return self.store.request_autolabel(payload)
        if path=='/api/studio/source-row-note': return self.store.source_row_note(payload)
        if path=='/api/studio/photo-selection': return self.store.save_photo_selection(payload)
        if path=='/api/studio/complete-review': return self.store.complete_review(payload)
        if path=='/api/studio/review': return self.store.save_review(payload)
        if path=='/api/studio/era-review': return self.store.review_era(payload)
        if self.jobs:
            if path=='/api/studio/train/preview': return self.jobs.preview('train')
            if path=='/api/studio/train': return self.jobs.start(payload, 'train')
            if path=='/api/studio/connection': return self.connection.connect(payload)
            if path=='/api/studio/connection/disconnect': return self.connection.disconnect()
            if path=='/api/studio/acq-comparison/preview': return self.exchange.preview(payload)
            if path=='/api/studio/acq-comparison/run': return self.exchange.start(payload)
        raise ValueError('Cloud model jobs are not connected yet. No local training or paid call was started.')


class CloudApp:
    def __init__(self, store, model_worker=False):
        self.token = secrets.token_urlsafe(32)
        self.rows, self.batch = {}, []
        self.studio = CloudStudio(self,store,model_worker)

    def get_studio(self): return self.studio


def from_env(*, allow_model_worker=False):
    required = ['SUPABASE_PROJECT_REF','STUDIO_DATABASE_URL','STUDIO_WORKSPACE_ID','STUDIO_STORAGE_SECRET']
    missing = [k for k in required if not os.environ.get(k)]
    if missing: raise ValueError('Cloud review configuration missing: '+', '.join(missing))
    database = Database(os.environ['STUDIO_DATABASE_URL'],os.environ['STUDIO_WORKSPACE_ID'])
    database.verify()
    cache = Path(os.environ.get('STUDIO_CACHE_DIR',str(DATA_ROOT/'cloud-cache'))).expanduser()
    if not cache.is_absolute(): raise ValueError('STUDIO_CACHE_DIR must be an absolute path')
    storage = PrivateStorage(os.environ['SUPABASE_PROJECT_REF'],os.environ['STUDIO_STORAGE_SECRET'],cache)
    return CloudApp(SupabaseStore(database,storage), model_worker=allow_model_worker and os.environ.get('STUDIO_MODEL_WORKER')=='1')
