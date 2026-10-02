"""Cloud-data review runtime. UI remains loopback-only until hosted auth is added."""
import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cloud_storage import PrivateStorage
from cloud_store import Database, SupabaseStore
from config import DATA_ROOT


class CloudStudio:
    def __init__(self, app, store, model_worker=False):
        self.app, self.store = app, store
        self.jobs = self.connection = self.exchange = None
        if model_worker:
            from cloud_training import SupabaseJobs
            from acq_exchange import Exchange
            from connected_worker import ConnectedWorker
            self.jobs = SupabaseJobs(store)
            self.exchange = Exchange(self.jobs)
            self.connection = ConnectedWorker(self.exchange)

    def get(self, path):
        parsed = urlparse(path)
        args = {k:v[0] for k,v in parse_qs(parsed.query).items()}
        if parsed.path=='/api/studio/source-rows': return self.store.source_rows(args)
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
        if path=='/api/studio/autolabel': return self.store.request_autolabel(payload)
        if path=='/api/studio/source-row-note': return self.store.source_row_note(payload)
        if path=='/api/studio/photo-selection': return self.store.save_photo_selection(payload)
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
