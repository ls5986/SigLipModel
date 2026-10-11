"""Resumable, rate-limited provider history enrichment; never edits frozen evidence."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import logging
import threading
from urllib.parse import urlparse

PREFIX='provider-history-v1:'
STATUS=PREFIX+'status'
FIELDS='HistoryTransactionalKey,ResourceName,ResourceRecordKey,FieldName,PreviousValue,NewValue,ModificationTimestamp,ChangeType'


def summarize(events):
    drops=[]
    for row in events:
        if row.get('FieldName')!='ListPrice':continue
        try:
            old,new=Decimal(str(row.get('PreviousValue'))),Decimal(str(row.get('NewValue')))
            if not old.is_finite() or not new.is_finite() or old<=0 or new<=0:continue
        except (InvalidOperation,ValueError):continue
        if new<old:drops.append({'from':float(old),'to':float(new),'amount':float(old-new),'provider_modified_at':row.get('ModificationTimestamp')})
    return {'recorded_drop_count':len(drops),'recorded_drop_total':sum(x['amount'] for x in drops),'drops':drops}


async def fetch_history(client,key):
    token=await client._access_token()
    path='/trestle/odata/HistoryTransactional'
    params={'$filter':"ResourceName eq 'Property' and ResourceRecordKey eq '"+key.replace("'","''")+"'",'$select':FIELDS,'$top':200}
    events={}
    complete=False
    for page in range(5):
        response=await client.http.get(path,params=params,headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
        response.raise_for_status()
        body=response.json()
        if not isinstance(body.get('value'),list):raise ValueError('Invalid history collection')
        for row in body['value']:
            if str(row.get('ResourceRecordKey'))!=key or row.get('ResourceName')!='Property':raise ValueError('History listing identity mismatch')
            ident=row.get('HistoryTransactionalKey')
            if not ident:raise ValueError('History event key missing')
            events[str(ident)]={name:row.get(name) for name in FIELDS.split(',')}
        next_link=body.get('@odata.nextLink')
        if not next_link:
            complete=True
            break
        parsed=urlparse(next_link)
        if parsed.scheme and parsed.scheme!='https':raise ValueError('Unsafe history pagination')
        if parsed.netloc and parsed.netloc!='api.cotality.com':raise ValueError('Unsafe history pagination')
        if not parsed.path.startswith('/trestle/odata/HistoryTransactional'):raise ValueError('Unsafe history pagination')
        path=next_link;params=None
        await asyncio.sleep(1)
    rows=sorted(events.values(),key=lambda row:(row.get('ModificationTimestamp') or '',str(row['HistoryTransactionalKey'])))
    return {'status':'available' if rows else 'no_events_returned','listing_key':key,'source':'TRESTLE_HISTORY_TRANSACTIONAL','fetched_at':datetime.now(timezone.utc).isoformat(),'pagination_complete':complete,'coverage':'provider_returned_history_not_guaranteed_lifetime','events':rows,**summarize(rows)}


def save(store,key,payload):
    old=store.document(key) or {}
    return store.save_document(key,payload,old.get('revision',0))


async def backfill(store):
    from event_media_recovery import HostedTrestleClient
    client=None
    counts={'processed':0,'with_events':0,'no_events':0,'cached':0}
    try:
        prior=store.document(STATUS) or {}
        if prior.get('status') in {'blocked','credentials_missing'}:return
        client=HostedTrestleClient()
        with store.database.connect() as db:
            keys=[row['listing_key'] for row in db.execute('''SELECT DISTINCT l.listing_key
              FROM acq_training.property_event_roles r
              JOIN acq_training.listing_events l ON l.workspace_id=r.workspace_id AND l.id=r.listing_event_id
              JOIN acq_training.source_rows s ON s.workspace_id=r.workspace_id AND s.id=r.source_row_id
              WHERE r.workspace_id=%s AND r.revision=0 AND l.listing_key IS NOT NULL
              AND s.source_import_id=(SELECT id FROM acq_training.source_imports WHERE workspace_id=%s
                AND schema_version='paired-workbook-v1' ORDER BY imported_at DESC,id DESC LIMIT 1)
              ORDER BY l.listing_key''',(store.workspace,store.workspace)).fetchall()]
        save(store,STATUS,{'status':'running','total':len(keys),**counts})
        for key in keys:
            key=str(key)
            existing=store.document(PREFIX+key)
            if existing and existing.get('pagination_complete'):
                counts['cached']+=1
                continue
            payload=await fetch_history(client,key)
            save(store,PREFIX+key,payload)
            counts['processed']+=1
            counts['with_events' if payload['events'] else 'no_events']+=1
            if counts['processed']<=3 or counts['processed']%25==0:
                save(store,STATUS,{'status':'running','total':len(keys),**counts})
                logging.warning('provider_history_backfill status=running processed=%d with_events=%d no_events=%d',counts['processed'],counts['with_events'],counts['no_events'])
            await asyncio.sleep(1)
        save(store,STATUS,{'status':'completed','total':len(keys),**counts})
        logging.warning('provider_history_backfill status=completed processed=%d with_events=%d',counts['processed'],counts['with_events'])
    except Exception as exc:
        response=getattr(exc,'response',None)
        code=getattr(response,'status_code',None)
        state='blocked' if code in (400,401,403,404) else 'failed'
        if client is None and isinstance(exc,ValueError):state='credentials_missing'
        save(store,STATUS,{'status':state,'http_status':code,'error_type':type(exc).__name__,**counts})
        logging.warning('provider_history_backfill status=%s http_status=%s error_type=%s',state,code,type(exc).__name__)
    finally:
        if client:await client.close()


def start(store):
    threading.Thread(target=lambda:asyncio.run(backfill(store)),name='provider-history-backfill',daemon=True).start()
