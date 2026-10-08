"""Explicit user-requested local-only automated silver scoring, never human truth.
No MLS photo or remark is sent to an external model API. Frozen SigLIP2 images,
local sentence embeddings and negation-aware text rules. No training or MLS calls.
"""
from datetime import datetime, timezone
import hashlib,json,math,os,re,time
POLICY='paired-local-siglip2-text-silver-v1'
DATED=[
 'A real estate photograph of a dated original home interior with older cabinets, carpet and finishes needing updating.',
 'A real estate photograph of a well maintained but original dated kitchen or bathroom that has not been remodeled.',
 'A real estate photograph of a partly updated home interior with remaining dated fixtures and finishes.'
]
UPDATED=[
 'A real estate photograph of a fully renovated modern home interior with new cabinets and contemporary finishes.',
 'A real estate photograph of a completely updated modern kitchen or bathroom.',
 'A real estate photograph of a newly remodeled turnkey home interior with modern fixtures and finishes.'
]
STAGING=['A digitally rendered or virtually staged artificial real estate interior image.','An authentic real estate photograph of an actual furnished or empty room.']
def now():return datetime.now(timezone.utc).isoformat()
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
def axis(decision='INSUFFICIENT_EVIDENCE',strength=None,reason='Insufficient evidence',quotes=None):return {'decision':decision,'strength':strength,'reason':reason,'supporting_quotes':quotes or []}
def aggregate(photos):
 usable=[p for p in photos if p.get('exclusion')=='none' and p.get('room') in {'kitchen','bathroom','living','bedroom','other_interior'} and p.get('decision') in {'TARGET','NOT_TARGET'}]
 if not usable:return axis(reason='No usable interior condition evidence')
 positive=[p for p in usable if p['decision']=='TARGET']
 if positive:
  w=lambda p:2 if p['room'] in {'kitchen','bathroom'} else 1
  return axis('TARGET',round(50+50*sum(map(w,positive))/sum(map(w,usable))),f'{len(positive)} of {len(usable)} usable interiors match dated prompts; kitchen/bath weighted twice')
 return axis('NOT_TARGET',reason=f'{len(usable)} usable interiors match updated prompts; unseen rooms remain unknown')
def matches(text,pattern):
 out=[]
 for m in re.finditer(pattern,text,re.I):
  if re.search(r'\b(?:no|not|without|never)\s+(?:any\s+|a\s+)?$',text[max(0,m.start()-45):m.start()],re.I):continue
  out.append(text[m.start():m.end()])
 return out
def metadata_score(meta,private):
 sources=[str(meta.get('public_remarks') or ''),str(private or '')]
 target=r'\b(?:dated|original (?:condition|kitchen|bathroom|bath|finishes|interior)|needs? (?:some |a little |major )?(?:updating|renovation|work|repairs)|fixer(?:[ -]upper)?|TLC|deferred maintenance|sweat equity|handyman|contractor special|partially updated|partly renovated)\b'
 updated=r'\b(?:fully (?:updated|renovated|remodeled)|completely (?:updated|renovated|remodeled)|newly (?:renovated|remodeled)|recently (?:renovated|remodeled)|updated throughout|remodeled throughout|move[ -]in ready|turn[ -]?key)\b'
 positive=[q for text in sources for q in matches(text,target)]
 negative=[q for text in sources for q in matches(text,updated)]
 if positive:return axis('TARGET',85 if len(positive)>1 else 75,'Explicit dated/original/updating language; mixed updated/dated language remains TARGET',positive[:8])
 condition=meta.get('structured',{}).get('PropertyCondition')
 if condition and matches(json.dumps(condition),target):return axis('TARGET',75,'PropertyCondition explicitly indicates updating needs')
 if negative:return axis('NOT_TARGET',reason='Explicit remodeled/turnkey description; marketing remains weak evidence',quotes=negative[:8])
 return axis(reason='No explicit dated or fully updated evidence. Year built, trust/probate, investor, as-is, and prices alone are insufficient')
def persist(store,key,payload):
 old=store.document(key) or {}
 if old.get('status')=='completed':return old
 return store.save_document(key,payload,old.get('revision',0))
class LocalScorer:
 def __init__(self):
  from automatic_labels import SiglipLabels,PROMPTS
  from provision_semantic_encoder import verify,DIRECTORY
  from semantic_text import SemanticTextEncoder
  self.vision=SiglipLabels();self.rooms=PROMPTS;self.prompts=PROMPTS+DATED+UPDATED+STAGING
  self.text_info=verify();self.text=SemanticTextEncoder(DIRECTORY,self.text_info['checkpoint_sha256'])
 def photos(self,paths):
  from PIL import Image,ImageOps
  from automatic_labels import resolve
  ims=[]
  for path in paths:
   with Image.open(path) as im:ims.append(ImageOps.exif_transpose(im).convert('RGB'))
  inputs=self.vision.processor(text=self.prompts,images=ims,padding='max_length',truncation=True,max_length=64,return_tensors='pt')
  with self.vision.torch.inference_mode():scores=self.vision.model(**inputs).logits_per_image.float().cpu().tolist()
  out=[];n=len(self.rooms)
  for row in scores:
   info=resolve(row[:n]);ts=info['scores_uncalibrated']['photo_type'];typ=max(ts,key=ts.get);room=info['room']
   if typ=='interior':
    room=max(info['scores_uncalibrated']['room'],key=info['scores_uncalibrated']['room'].get)
    if room=='other':room='other_interior'
   exclusion='none' if typ=='interior' else 'floor_plan' if typ=='floor_plan' else 'unrelated' if typ in {'map','document','other'} else 'shared_amenity' if typ=='pool' else 'non_interior'
   staging=row[n+6]-row[n+7]
   if typ=='interior' and staging>2:exclusion='suspected_virtual_staging'
   target=sum(row[n:n+3])/3;updated=sum(row[n+3:n+6])/3;margin=target-updated
   value=axis(reason='Prompt evidence uncertain')
   if exclusion=='none' and ts[typ]>=.40:
    if margin>=.75:value=axis('TARGET',min(100,max(50,round(50+50/(1+math.exp(-min(20,margin)))))), 'Frozen SigLIP2 matches dated/original prompts')
    elif margin<=-.75:value=axis('NOT_TARGET',reason='Frozen SigLIP2 matches fully updated prompts')
   out.append({**value,'room':room,'photo_type':typ,'exclusion':exclusion,'target_logit':target,'updated_logit':updated,'margin':margin,'staging_margin':staging,'provenance':'FROZEN_SIGLIP2_ZERO_SHOT','backbone_revision':self.vision.revision})
  return out
 def semantic(self,text):
  if not text.strip():return {'available':False}
  features=self.text.transform([text]).toarray()[0].tolist()
  return {'available':True,'features':features,'checkpoint_sha256':self.text_info['checkpoint_sha256'],'dimension':len(features),'trained_head':False}
def inspect(store,batch,item,scorer):
 key='paired-auto-score:'+batch['batch_id']+':'+item['evidence_id'];previous=store.document(key)
 if previous and previous.get('status') in {'completed','failed'}:return previous
 persist(store,key,{'status':'running','evidence_id':item['evidence_id'],'policy':POLICY,'at':now()})
 try:
  with store.database.connect() as db:
   row=db.execute('SELECT s.*,l.raw_snapshot FROM acq_training.evidence_snapshots s JOIN acq_training.listing_events l ON l.workspace_id=s.workspace_id AND l.id=s.listing_event_id WHERE s.workspace_id=%s AND s.id=%s',(store.workspace,item['evidence_id'])).fetchone()
  if not row or row['evidence_sha256']!=item['evidence_sha256']:raise ValueError('Evidence changed')
  meta=row['metadata_snapshot'];raw=row['raw_snapshot'];ma=metadata_score(meta,raw.get('PrivateRemarks'))
  semantic=scorer.semantic((str(meta.get('public_remarks') or '')+'\n'+str(raw.get('PrivateRemarks') or ''))[:32000])
  photos=[];failures=[];manifest=row['photo_manifest']
  for offset in range(0,len(manifest),4):
   ckey=key+':photos:'+str(offset);old=store.document(ckey)
   if old and old.get('status')=='completed':photos.extend(old['photos']);failures.extend(old['failures']);continue
   paths=[];selected=[];unavailable=[]
   for i,p in enumerate(manifest[offset:offset+4],offset):
    try:
     with store.database.connect() as db:
      blocked=db.execute('SELECT 1 FROM acq_training.photos WHERE workspace_id=%s AND image_sha256=%s AND (revoked_at IS NOT NULL OR retention_until<=now()) LIMIT 1',(store.workspace,p['sha256'])).fetchone()
     if blocked:raise ValueError('Retention blocked')
     paths.append(store.storage.get(p['storage_bucket'],p['storage_object_key'],p['sha256']));selected.append((i,p['sha256']))
    except Exception as exc:unavailable.append({'photo_index':i,'sha256':p['sha256'],'error_kind':type(exc).__name__,'status':'unavailable'})
   scored=scorer.photos(paths) if paths else []
   rows=[{**r,'photo_index':i,'sha256':h} for (i,h),r in zip(selected,scored)]
   persist(store,ckey,{'status':'completed','photos':rows,'failures':unavailable,'at':now(),'policy':POLICY});photos.extend(rows);failures.extend(unavailable)
  image=aggregate(photos)
  overall='TARGET' if 'TARGET' in {image['decision'],ma['decision']} else 'NOT_TARGET' if image['decision']==ma['decision']=='NOT_TARGET' else 'INSUFFICIENT_EVIDENCE'
  result={'status':'completed','batch_id':batch['batch_id'],'evidence_id':item['evidence_id'],'evidence_sha256':item['evidence_sha256'],'group_id':item['group_id'],'event_role':item['event_role'],'listing_key':item['listing_key'],'image':image,'metadata':ma,'remarks_semantic':semantic,'overall':overall,'photos':photos,'unavailable_photos':failures,'photo_count_expected':len(manifest),'photo_count_scored':len(photos),'provenance':'AUTOMATED_SILVER','is_gold':False,'human_review_required':False,'policy':POLICY,'model':'local-frozen-SigLIP2+sentence-encoder+rules','at':now(),'runtime_commit':os.environ.get('RENDER_GIT_COMMIT'),'quality_flags':{'candidate_decision':item['candidate_decision'],'pair_decision':item['pair_decision'],'point_in_time_verified':meta.get('point_in_time_verified',False),'photo_era_verified':meta.get('photo_era_verified',False),'staging_detection':'heuristic','private_data_external_model_egress':False,'scores_are_probabilities':False}}
  result['result_sha256']=digest(result);return persist(store,key,result)
 except Exception as exc:return persist(store,key,{'status':'failed','evidence_id':item['evidence_id'],'at':now(),'policy':POLICY,'error_kind':type(exc).__name__,'error':'Scoring failed; no invented score; explicit retry only'})
def run():
 from cloud_runtime import from_env
 batch_id=os.environ['STUDIO_PAIRED_AUTO_BATCH'];store=from_env().get_studio().store
 if store.workspace!=os.environ.get('STUDIO_LABEL_WORKER_WORKSPACE'):raise ValueError('Workspace mismatch')
 key='paired-auto-batch:'+batch_id;batch=store.document(key)
 if not batch or batch['batch_id']!=batch_id or not batch.get('freeze_authorized'):raise ValueError('Batch not authorized')
 persist(store,key,{**batch,'status':'loading','started_at':now(),'policy':POLICY,'private_data_external_model_egress':False})
 scorer=LocalScorer();persist(store,key,{**store.document(key),'status':'running','backbone_revision':scorer.vision.revision,'text_checkpoint':scorer.text_info})
 for index,item in enumerate(batch['items']):
  result=inspect(store,batch,item,scorer)
  print(json.dumps({'batch':batch_id,'processed':index+1,'total':len(batch['items']),'status':result['status']}),flush=True)
  persist(store,key,{**store.document(key),'status':'running','heartbeat_at':now(),'processed':index+1})
 with store.database.connect() as db:
  rows=db.execute("SELECT payload->>'status' status,count(*) n FROM acq_training.studio_state WHERE workspace_id=%s AND kind='document' AND item_id LIKE %s AND item_id NOT LIKE '%%:photos:%%' GROUP BY payload->>'status'",(store.workspace,'paired-auto-score:'+batch_id+':%')).fetchall()
 counts={r['status']:r['n'] for r in rows}
 persist(store,key,{**store.document(key),'status':'scored' if counts.get('completed',0)==len(batch['items']) else 'blocked','counts':counts,'finished_at':now(),'freeze_pending':True})
 while True:time.sleep(30)
if __name__=='__main__':run()
