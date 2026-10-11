"""Bounded historical acquisition lookup. Keeps original snapshots and reviews intact."""
import asyncio
from datetime import date,datetime,timezone
import hashlib
import os
import re
import threading

KEY='acquisition-recovery:'

def digits(value):
    return re.sub(r'\D','',str(value or ''))

def parcel(value):
    value=digits(value)
    return value+'00' if len(value)==8 else value

def day(value):
    try:return date.fromisoformat(str(value)[:10])
    except (ValueError,TypeError):return None

def acquisition_candidates(rows,source):
    dates=[d for d in (day(source.get('prior_date')),day(source.get('prior_recording_date'))) if d]
    last=day(source.get('last_date'))
    number=re.match(r'^\s*(\d+)',source.get('address',''))
    unit=str(source.get('unit') or '').strip().casefold()
    result={}
    for listing in rows:
        closed=day(listing.get('CloseDate'))
        if not closed or not dates or min(abs((closed-d).days) for d in dates)>14:continue
        if last and abs((closed-last).days)<min(abs((closed-d).days) for d in dates):continue
        if parcel(listing.get('ParcelNumber'))!=parcel(source['apn']):continue
        if number and str(listing.get('StreetNumber') or '').strip()!=number.group(1):continue
        candidate_unit=str(listing.get('UnitNumber') or '').strip().casefold()
        if candidate_unit!=unit:continue
        if listing.get('ListingKey'):result[str(listing['ListingKey'])]=listing
    return list(result.values())

async def recover(store,payload):
    from event_media_recovery import HostedTrestleClient
    from backfill_validation_media import download_image,storage_client,storage_upload
    payload={**payload,'lookup_policy':'historical-apn-address-v2'}
    source=payload['source'];client=HostedTrestleClient();storage=None
    try:
        token=await client._access_token()
        variants={source['apn'],digits(source['apn'])}
        apn=digits(source['apn'])
        if len(apn)==10:
            variants.add(apn[:3]+'-'+apn[3:6]+'-'+apn[6:8]+'-'+apn[8:])
            if apn.endswith('00'):
                variants.update({apn[:8],apn[:3]+'-'+apn[3:6]+'-'+apn[6:8]})
        filter_text=' or '.join("ParcelNumber eq '"+v.replace("'","''")+"'" for v in sorted(variants))
        response=await client.http.get('/trestle/odata/Property',params={'$filter':filter_text,'$top':100,'$orderby':'CloseDate desc'},headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
        response.raise_for_status();body=response.json()
        if body.get('@odata.nextLink'):return {**payload,'status':'needs_review','reason':'Historical property lookup exceeded bounded result limit'}
        candidates=acquisition_candidates(body.get('value') or [],source)
        if not candidates:
            with store.database.connect() as db:
                current=db.execute('SELECT odata_json FROM acq_training.property_target_review WHERE workspace_id=%s AND group_id=%s',(store.workspace,payload['group_id'])).fetchone()
            number=re.match(r'^\s*(\d+)',source.get('address',''))
            postal=str(current['odata_json'].get('PostalCode') or '').split('-')[0]
            if number and re.fullmatch(r'\d{5}',postal):
                address_filter="StreetNumber eq '"+number.group(1)+"' and PostalCode eq '"+postal+"'"
                extra=await client.http.get('/trestle/odata/Property',params={'$filter':address_filter,'$top':100,'$orderby':'CloseDate desc'},headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
                extra.raise_for_status();extra_body=extra.json()
                if extra_body.get('@odata.nextLink'):return {**payload,'status':'needs_review','reason':'Address lookup exceeded bounded result limit'}
                body['value']=(body.get('value') or [])+(extra_body.get('value') or [])
                candidates=acquisition_candidates(body['value'],source)
        if len(candidates)!=1:return {**payload,'status':'missing' if not candidates else 'needs_review','reason':'No unique APN, unit and acquisition-date match','candidate_count':len(candidates),'provider_records':len(body.get('value') or []),'examined_listings':[{'listing_key':v.get('ListingKey'),'close_date':v.get('CloseDate'),'parcel':v.get('ParcelNumber')} for v in body.get('value') or []]}
        listing=candidates[0];listing_key=str(listing['ListingKey']);media=await client.media_records(listing_key,max_images=100)
        project,storage=storage_client();photos=[];failures=0
        for record in media.records:
            try:
                blob=await download_image(client,record.url);digest=hashlib.sha256(blob).hexdigest()
                key='acquisition-recovery/'+payload['group_id']+'/'+listing_key+'/'+digest+'.jpg'
                storage_upload(storage,project,key,blob)
                photos.append({'photo_id':listing_key+':'+str(record.media_key),'sha256':digest,'storage_bucket':'acq-training-private','storage_object_key':key,'room':'other','provider_media_key':record.media_key,'provider_modified_at':record.modification_timestamp,'provenance':{'listing_key':listing_key,'photo_timing':'UNVERIFIED','source':'TRESTLE_LISTING_MEDIA'}})
            except Exception:failures+=1
        return {**payload,'status':'recovered','listing':listing,'photos':photos,'photo_failures':failures,'media_truncated':media.truncated,'photo_timing':'UNVERIFIED','transaction_match':'APN_UNIT_PRIOR_DATE','recovered_at':datetime.now(timezone.utc).isoformat()}
    finally:
        await client.close()
        if storage:storage.close()

def poll(store):
    with store.database.connect() as db:
        row=db.execute("SELECT item_id,payload,revision FROM acq_training.studio_state WHERE workspace_id=%s AND kind='document' AND item_id LIKE 'acquisition-recovery:%%' AND (payload->>'status'='queued' OR (payload->>'status'='missing' AND payload->>'lookup_policy' IS DISTINCT FROM 'historical-apn-address-v2')) ORDER BY item_id LIMIT 1",(store.workspace,)).fetchone()
    if not row:return False
    payload=row['payload']
    try:result=asyncio.run(recover(store,payload))
    except Exception as exc:result={**payload,'status':'blocked','error_code':type(exc).__name__}
    if result.get('status')=='recovered':
        try:
            apply_recovered(store,payload['group_id'],result)
            result['evidence_applied']=True
        except Exception as exc:
            result['apply_error_code']=type(exc).__name__
    store.save_document(row['item_id'],result,row['revision'])
    return True

def start(store):
    if os.environ.get('STUDIO_ACQUISITION_RECOVERY_ENABLED')!='true':return None
    stop=threading.Event()
    def run():
        while not stop.is_set():
            try:worked=poll(store)
            except Exception:worked=False
            stop.wait(2 if worked else 20)
    threading.Thread(target=run,name='acquisition-recovery',daemon=True).start()
    return stop

def apply_recovered(store,group_id,result):
    """Archive the previous sheet row, then replace evidence and reset its two decisions."""
    from psycopg.types.json import Jsonb
    with store.database.connect() as db:
        row=db.execute('SELECT * FROM acq_training.property_target_review WHERE workspace_id=%s AND group_id=%s FOR UPDATE',(store.workspace,group_id)).fetchone()
        listing=result['listing'];key=str(listing['ListingKey'])
        if row['listing_key']==key:return
        archive='acquisition-repair-original:'+group_id
        if not store.database.state(db,'document',archive):
            original=dict(row)
            for field in ('workspace_id','group_id','dataset_id','frozen_at','reviewed_at'):
                if original.get(field) is not None:original[field]=str(original[field])
            store.database.save(db,'document',archive,0,original)
        identity='recovered-acquisition:'+key
        event={'property_id':key,'event_role':'acquisition','evidence_id':identity,'photos':result['photos'],'remarks':listing.get('PublicRemarks') or '', 'structured':{},'provenance':{'transaction_match':result['transaction_match'],'photo_timing':'UNVERIFIED'}}
        db.execute('''UPDATE acq_training.property_target_review SET listing_key=%s,evidence_identity=%s,
            event_snapshot=%s,odata_json=%s,image_decision=NULL,image_strength=NULL,
            metadata_decision=NULL,metadata_strength=NULL,reviewed_at=NULL,reviewed_by=NULL,
            notes='',revision=revision+1,frozen_at=now() WHERE workspace_id=%s AND group_id=%s''',
            (key,identity,Jsonb(event),Jsonb(listing),store.workspace,group_id))
        # Advance the same compare-and-swap history that human saves use.
        store.database.save(db,'document','simple-target-review:'+group_id,row['revision'],
            {'origin':'EVIDENCE_REPAIR','group_id':group_id,'overall':row['overall_decision'],
             'image':None,'metadata':None,'notes':'','evidence_identity':identity,
             'previous_review_archived_as':archive,'at':datetime.now(timezone.utc).isoformat()})
