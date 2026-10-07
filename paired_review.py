"""Independent, evidence-bound reviews for prior-sale and later-sale candidates."""
import json
from uuid import UUID
from target_review import validate_answer

LATEST = '''SELECT id FROM acq_training.source_imports WHERE workspace_id=%s
 AND schema_version='paired-workbook-v1' ORDER BY imported_at DESC,id DESC LIMIT 1'''
ROLE_DECISIONS={'human_confirmed','wrong_property','wrong_era','ambiguous','source_only','provider_error'}
CONDITIONS={'C1_NEW','C2_LIKE_NEW','C3_WELL_MAINTAINED','C4_AVERAGE_FUNCTIONAL','C5_REHAB_NEEDED','C6_SEVERE_DISTRESS','UNKNOWN'}
MODERNIZATION={'ORIGINAL','PARTIALLY_UPDATED','UPDATED','FULLY_REMODELED','UNKNOWN'}

def validate(payload):
 allowed={'group_id','evidence_id','role_id','expected_revision','overall','image','metadata','notes','role_decision','condition','modernization','excluded_photos','private_signal_reviews'}
 if not isinstance(payload,dict) or set(payload)-allowed:raise ValueError('Invalid review fields')
 base={k:v for k,v in payload.items() if k in {'group_id','expected_revision','overall','image','metadata','notes'}}
 answer,revision=validate_answer(base)
 for key in ('evidence_id','role_id'):answer[key]=str(UUID(str(payload.get(key))))
 if payload.get('role_decision') not in ROLE_DECISIONS:raise ValueError('Verify the property and listing era')
 if payload.get('condition','UNKNOWN') not in CONDITIONS:raise ValueError('Invalid condition')
 if payload.get('modernization','UNKNOWN') not in MODERNIZATION:raise ValueError('Invalid modernization')
 answer.update(role_decision=payload['role_decision'],condition=payload.get('condition','UNKNOWN'),modernization=payload.get('modernization','UNKNOWN'))
 excluded=payload.get('excluded_photos',{})
 if not isinstance(excluded,dict) or len(excluded)>1000 or any(not isinstance(k,str) or len(k)!=64 or v not in {'floor_plan','virtual_staging','shared_amenity','unrelated'} for k,v in excluded.items()):raise ValueError('Invalid photo exclusions')
 answer['excluded_photos']=excluded
 private=payload.get('private_signal_reviews',{})
 if not isinstance(private,dict) or len(private)>1000:raise ValueError('Invalid private signal reviews')
 for key,state in private.items():
  str(UUID(str(key)))
  if state not in {'PRESENT','ABSENT','UNKNOWN'}:raise ValueError('Invalid signal state')
 answer['private_signal_reviews']=private
 return answer,revision

class PairedReview:
 def __init__(self,store):self.store,self.database=store,store.database
 def _import(self,db):
  row=db.execute(LATEST,(self.database.workspace,)).fetchone()
  if not row:raise ValueError('The corrected workbook has not been staged')
  return row['id']
 def queue(self):
  with self.database.connect() as db:
   imp=self._import(db)
   rows=db.execute('''SELECT s.id::text source_id,s.group_id::text group_id,s.address,s.normalized_apn,s.normalized_unit,
    g.protected_test,EXISTS(SELECT 1 FROM acq_training.evaluation_slice_groups sg JOIN acq_training.evaluation_slices es ON es.workspace_id=sg.workspace_id AND es.id=sg.slice_id WHERE sg.workspace_id=s.workspace_id AND sg.group_id=s.group_id AND es.name='corrected-prior-sale-human-review-50') protected_review, s.raw_snapshot->'simplified'->>'Prior Sale MLS' prior_mls,
    s.raw_snapshot->'simplified'->>'Last Sale MLS' last_mls,
    (EXISTS(SELECT 1 FROM acq_training.property_label_events l WHERE l.workspace_id=s.workspace_id AND l.group_id=s.group_id AND l.label_axis='overall') AND NOT EXISTS(SELECT 1 FROM acq_training.property_event_roles r JOIN acq_training.evidence_snapshots es ON es.workspace_id=r.workspace_id AND es.listing_event_id=r.listing_event_id AND es.metadata_snapshot->>'role'=r.event_role WHERE r.workspace_id=s.workspace_id AND r.source_row_id=s.id AND r.revision=0 AND NOT EXISTS(SELECT 1 FROM acq_training.property_label_events l WHERE l.workspace_id=es.workspace_id AND l.evidence_snapshot_id=es.id AND l.label_axis='overall'))) reviewed
    FROM acq_training.source_rows s JOIN acq_training.property_groups g ON g.workspace_id=s.workspace_id AND g.id=s.group_id
    WHERE s.workspace_id=%s AND s.source_import_id=%s
    ORDER BY protected_review DESC,g.protected_test DESC,(s.raw_snapshot->'simplified'->>'Prior Sale MLS' IS NOT NULL AND s.raw_snapshot->'simplified'->>'Last Sale MLS' IS NOT NULL) DESC,s.source_row_number''',(self.database.workspace,imp)).fetchall()
  return {'items':[dict(r) for r in rows],'total':len(rows)}
 def detail(self,group):
  group=str(UUID(str(group)))
  with self.database.connect() as db:
   imp=self._import(db)
   source=db.execute('''SELECT * FROM acq_training.source_rows WHERE workspace_id=%s AND source_import_id=%s AND group_id=%s''',(self.database.workspace,imp,group)).fetchone()
   if not source:raise ValueError('Property is outside the corrected workbook')
   events=db.execute('''SELECT r.id::text role_id,r.event_role,r.decision,r.audit,r.revision role_revision,
    l.listing_key,l.listing_id,l.standard_status,l.raw_snapshot,s.id::text evidence_id,s.metadata_snapshot,s.photo_manifest,s.evidence_sha256,
    t.close_date,t.recording_date,t.close_price
    FROM acq_training.property_event_roles r
    LEFT JOIN acq_training.listing_events l ON l.workspace_id=r.workspace_id AND l.id=r.listing_event_id
    LEFT JOIN LATERAL (SELECT es.* FROM acq_training.evidence_snapshots es WHERE es.workspace_id=l.workspace_id AND es.listing_event_id=l.id AND es.metadata_snapshot->>'role'=r.event_role ORDER BY es.evidence_sha256 DESC LIMIT 1)s ON true
    LEFT JOIN acq_training.sale_transactions t ON t.workspace_id=r.workspace_id AND t.id=r.transaction_id
    WHERE r.workspace_id=%s AND r.source_row_id=%s AND r.revision=0 ORDER BY r.event_role''',(self.database.workspace,source['id'])).fetchall()
   result=[]
   for row in events:
    event=dict(row)
    event['signals']=[];event['review']=None;event['revision']=0
    if event['evidence_id']:
     labels=db.execute('''SELECT DISTINCT ON(label_axis) label_axis,label_value,answer,revision,reviewer,created_at FROM acq_training.property_label_events
      WHERE workspace_id=%s AND evidence_snapshot_id=%s ORDER BY label_axis,revision DESC''',(self.database.workspace,event['evidence_id'])).fetchall()
     event['revision']=max((l['revision'] for l in labels),default=0)
     event['review']={l['label_axis']:l['answer'] for l in labels} or None
     role=db.execute('''SELECT decision,audit FROM acq_training.property_event_roles WHERE workspace_id=%s AND source_row_id=%s AND event_role=%s ORDER BY revision DESC LIMIT 1''',(self.database.workspace,source['id'],event['event_role'])).fetchone()
     event['decision']=role['decision']
     event['audit']=role['audit']
     event['signals']=[dict(r) for r in db.execute('''SELECT id::text signal_id,source_field,signal_name,state,snippet,start_offset,end_offset,review_status FROM acq_training.remark_signal_events
      WHERE workspace_id=%s AND evidence_snapshot_id=%s AND state<>'UNKNOWN' ORDER BY source_field,start_offset''',(self.database.workspace,event['evidence_id'])).fetchall()]
    event['previous_review']=None
    if event['listing_key']:
     old=db.execute('''SELECT image_decision,image_strength,metadata_decision,metadata_strength,overall_decision,notes,reviewed_at FROM acq_training.property_target_review WHERE workspace_id=%s AND group_id=%s AND listing_key=%s AND reviewed_at IS NOT NULL''',(self.database.workspace,group,event['listing_key'])).fetchone()
     if old:event['previous_review']=dict(old)
    result.append(event)
  return json.loads(json.dumps({'group_id':group,'address':source['address'],'apn':source['normalized_apn'],'unit':source['normalized_unit'],'source':source['raw_snapshot']['simplified'],'events':result,'warning':'Candidate roles and suggested targets need your review. Later-sale evidence stays separate from acquisition features.'},default=str))
 def save(self,payload,reviewer):
  answer,expected=validate(payload)
  with self.database.connect() as db:
   imp=self._import(db)
   db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(str(self.database.workspace)+answer['group_id'],))
   row=db.execute('''SELECT r.*,s.photo_manifest FROM acq_training.property_event_roles r
    JOIN acq_training.source_rows src ON src.workspace_id=r.workspace_id AND src.id=r.source_row_id
    JOIN acq_training.evidence_snapshots s ON s.workspace_id=r.workspace_id AND s.listing_event_id=r.listing_event_id AND s.group_id=r.group_id AND s.metadata_snapshot->>'role'=r.event_role
    WHERE r.workspace_id=%s AND r.group_id=%s AND r.id=%s AND s.id=%s AND src.source_import_id=%s AND r.revision=0''',(self.database.workspace,answer['group_id'],answer['role_id'],answer['evidence_id'],imp)).fetchone()
   if not row:raise ValueError('The selected listing evidence is outside this review')
   signal_ids={str(s['id']) for s in db.execute("SELECT id FROM acq_training.remark_signal_events WHERE workspace_id=%s AND evidence_snapshot_id=%s AND source_field='PrivateRemarks'",(self.database.workspace,answer['evidence_id'])).fetchall()}
   if set(answer['private_signal_reviews'])-signal_ids:raise ValueError('Signal is outside this private listing evidence')
   if answer['role_decision']=='human_confirmed':
    conflict=db.execute('''SELECT 1 FROM acq_training.property_event_roles r WHERE r.workspace_id=%s AND r.listing_event_id=%s AND r.transaction_id<>%s AND r.decision='human_confirmed' AND r.revision=(SELECT max(q.revision) FROM acq_training.property_event_roles q WHERE q.workspace_id=r.workspace_id AND q.source_row_id=r.source_row_id AND q.event_role=r.event_role) LIMIT 1''',(self.database.workspace,row['listing_event_id'],row['transaction_id'])).fetchone()
    if conflict:raise ValueError('This MLS is already confirmed for a different transaction. Resolve the transaction conflict first.')
   hashes={p['sha256'] for p in row['photo_manifest']}
   if set(answer['excluded_photos'])-hashes:raise ValueError('Excluded photo is outside this listing')
   if not (hashes-set(answer['excluded_photos'])) and answer['image']['decision']!='INSUFFICIENT_EVIDENCE':raise ValueError('No usable stored photos: choose Cannot tell')
   db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(str(self.database.workspace)+answer['evidence_id'],))
   current=db.execute('SELECT coalesce(max(revision),0) r FROM acq_training.property_label_events WHERE workspace_id=%s AND evidence_snapshot_id=%s',(self.database.workspace,answer['evidence_id'])).fetchone()['r']
   if current!=expected:raise RuntimeError('This listing changed in another window. Reload before saving.')
   revision=current+1
   values={'image':answer['image'],'metadata':answer['metadata'],'overall':{'decision':answer['overall']},'condition':{'decision':answer['condition']},'modernization':{'decision':answer['modernization']},'notes':{'text':answer['notes']},'photo_exclusions':{'excluded':answer['excluded_photos']}}
   values.update({'private_signal:'+key:{'decision':value} for key,value in answer['private_signal_reviews'].items()})
   from psycopg.types.json import Jsonb
   for axis,value in values.items():
    db.execute('''INSERT INTO acq_training.property_label_events(workspace_id,group_id,evidence_snapshot_id,label_axis,label_value,answer,provenance,review_status,reviewer,revision)
     VALUES(%s,%s,%s,%s,%s,%s,'HUMAN_REVIEWED','approved',%s,%s)''',(self.database.workspace,answer['group_id'],answer['evidence_id'],axis,value.get('decision','NOTE'),Jsonb(value),reviewer,revision))
   role_revision=db.execute('SELECT max(revision)+1 next FROM acq_training.property_event_roles WHERE workspace_id=%s AND source_row_id=%s AND event_role=%s',(self.database.workspace,row['source_row_id'],row['event_role'])).fetchone()['next']
   db.execute('''INSERT INTO acq_training.property_event_roles(workspace_id,source_row_id,group_id,listing_event_id,transaction_id,event_role,decision,provenance,reviewer,reviewed_at,revision,audit,evidence_sha256)
    VALUES(%s,%s,%s,%s,%s,%s,%s,'HUMAN_REVIEWED',%s,now(),%s,%s,%s)''',(self.database.workspace,row['source_row_id'],row['group_id'],row['listing_event_id'],row['transaction_id'],row['event_role'],answer['role_decision'],reviewer,role_revision,Jsonb(row['audit']),row['evidence_sha256']))
  return {'revision':revision,'saved':True}
 def image(self,evidence,digest):
  evidence=str(UUID(str(evidence)))
  with self.database.connect() as db:
   imp=self._import(db)
   row=db.execute('''SELECT s.photo_manifest FROM acq_training.evidence_snapshots s
    JOIN acq_training.property_event_roles r ON r.workspace_id=s.workspace_id AND r.listing_event_id=s.listing_event_id AND r.group_id=s.group_id AND s.metadata_snapshot->>'role'=r.event_role
    JOIN acq_training.source_rows src ON src.workspace_id=r.workspace_id AND src.id=r.source_row_id
    WHERE s.workspace_id=%s AND s.id=%s AND src.source_import_id=%s LIMIT 1''',(self.database.workspace,evidence,imp)).fetchone()
   photo=next((p for p in (row['photo_manifest'] if row else []) if p.get('sha256')==digest),None)
   if not photo:raise ValueError('Photo is outside the selected evidence')
   blocked=db.execute('''SELECT 1 FROM acq_training.photos WHERE workspace_id=%s AND image_sha256=%s AND (revoked_at IS NOT NULL OR retention_until<=now()) LIMIT 1''',(self.database.workspace,digest)).fetchone()
   if blocked:raise ValueError('Photo retention or access has changed')
  return self.store.storage.get(photo['storage_bucket'],photo['storage_object_key'],digest)
