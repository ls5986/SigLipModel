"""Cloud-data review runtime. UI remains loopback-only until hosted auth is added."""
import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cloud_storage import PrivateStorage
from cloud_store import Database, SupabaseStore
from config import DATA_ROOT


class CloudStudio:
    def __init__(self, app, store):
        self.app, self.store = app, store

    def get(self, path):
        parsed = urlparse(path)
        args = {k:v[0] for k,v in parse_qs(parsed.query).items()}
        if parsed.path=='/api/studio/review-queue':
            return {**self.store.queue(args),'token':self.app.token}
        if parsed.path=='/api/studio/property':
            return self.store.property(args.get('id'))
        if parsed.path=='/api/studio/summary':
            queue=self.store.queue({'scope':'acquisitions','queue':'all','offset':0,'limit':1})
            return {'token':self.app.token,'storage':'supabase',
                    'capabilities':{'review':True,'training':False,'assessment':False},
                    'counts':queue['counts'],'photo_count':queue['photo_count'],
                    'metadata_policy':{'stored':'sanitized confirmed-listing snapshot',
                                       'model_feature_groups':12,'post_decision_outcomes':False},
                    'notice':'Reviews and photos use Supabase. Model jobs need the separate hosted worker cutover.'}
        raise ValueError('This action is not available in cloud review mode yet')

    def post(self, path, payload):
        if path=='/api/studio/review': return self.store.save_review(payload)
        if path=='/api/studio/era-review': return self.store.review_era(payload)
        raise ValueError('Cloud model jobs are not connected yet. No local training or paid call was started.')


class CloudApp:
    def __init__(self, store):
        self.token = secrets.token_urlsafe(32)
        self.rows, self.batch = {}, []
        self.studio = CloudStudio(self,store)

    def get_studio(self): return self.studio


def from_env():
    required = ['SUPABASE_PROJECT_REF','STUDIO_DATABASE_URL','STUDIO_WORKSPACE_ID','STUDIO_STORAGE_SECRET']
    missing = [k for k in required if not os.environ.get(k)]
    if missing: raise ValueError('Cloud review configuration missing: '+', '.join(missing))
    database = Database(os.environ['STUDIO_DATABASE_URL'],os.environ['STUDIO_WORKSPACE_ID'])
    database.verify()
    cache = Path(os.environ.get('STUDIO_CACHE_DIR',str(DATA_ROOT/'cloud-cache'))).expanduser()
    if not cache.is_absolute(): raise ValueError('STUDIO_CACHE_DIR must be an absolute path')
    storage = PrivateStorage(os.environ['SUPABASE_PROJECT_REF'],os.environ['STUDIO_STORAGE_SECRET'],cache)
    return CloudApp(SupabaseStore(database,storage))
