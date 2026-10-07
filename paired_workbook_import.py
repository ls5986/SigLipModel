"""Read corrected workbook; produce idempotent, parameterized staging batches.
No MLS network calls. Stored snapshots are supplied separately by an operator.
"""
import json,re,hashlib
from datetime import datetime,date
from pathlib import Path
from collections import Counter

def canonical(value):
 return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,default=lambda x:x.isoformat() if isinstance(x,(date,datetime)) else str(x))
def fingerprint(value): return hashlib.sha256(canonical(value).encode()).hexdigest()
def norm_apn(v): return re.sub(r'\D','',str(v or ''))
def norm_unit(v): return re.sub(r'[^A-Z0-9]','',str(v or '').upper())
def mls(v): return None if v is None or str(v).strip().casefold() in ('','none','nan') else str(v).strip()
def read_workbook(path):
 import openpyxl
 path=Path(path);w=openpyxl.load_workbook(path,read_only=True,data_only=True)
 def rows(n):
  a=list(w[n].values);return [dict(zip(a[0],r)) for r in a[1:]]
 out={'source_name':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'simplified':rows('Simplified'),'original':rows('Sheet1'),'audit':rows('MLS Match Audit')}
 assert len(out['simplified'])==len(out['audit'])==len(out['original'])
 return json.loads(canonical(out))

STRUCTURED=['YearBuilt','BedroomsTotal','BathroomsTotalInteger','LivingArea','LotSizeSquareFeet','PropertyType','PropertySubType','PropertyAttachedYN','City','PostalCode','MLSAreaMajor','OriginalListPrice','ListPrice','DaysOnMarket','CumulativeDaysOnMarket','PhotosCount','AssociationYN','AssociationFee','OccupantType','SpecialListingConditions','ListingTerms','PropertyCondition','Appliances','Flooring','Heating','Cooling','ParkingFeatures','PoolFeatures','Roof','ConstructionMaterials','Sewer','Utilities']
PATTERNS={
 'fixer_tlc':r'\b(?:fixer(?: upper)?|tlc|handyman)\b',
 'value_add_investor':r'\b(?:value[ -]add|sweat equity|investors?)\b',
 'deferred_maintenance':r'\b(?:deferred maintenance|needs? updating|original kitchen|dated)\b',
 'as_is':r'\bas[ -]is\b','remodeled_renovated':r'\b(?:remodeled|renovated)\b',
 'turnkey_move_in_ready':r'\b(?:turn[ -]?key|move[ -]in ready)\b',
 'updated_finishes':r'\b(?:updated (?:kitchen|bath)|quartz|stainless steel)\b',
 'major_rehab_risk':r'\b(?:foundation|structural|mold|fire damage|water damage|termite)\b',
 'unpermitted':r'\bunpermitted\b','permit_uncertain':r'\b(?:permits? unknown|permit status|verify permits?)\b',
 'cash_only':r'\bcash only\b','trust_probate':r'\b(?:trust sale|probate|court approval|estate sale)\b',
 'occupancy_access':r'\b(?:tenant occupied|occupied|vacant|do not disturb|appointment only)\b',
 'offer_instructions':r'\b(?:offer deadline|multiple offers|submit (?:all )?offers|backup offers)\b',
 'solar_hoa_obligation':r'\b(?:solar (?:lease|loan|ppa)|special assessment|hoa litigation)\b',
 'verification_uncertainty':r'\b(?:buyer to verify|verify all information|buyer and buyer.s agent to verify)\b',
 'room_dimension_boilerplate':r'\b(?:room dim|bed dimension|mandatory remarks none known)\b'}
def signals(raw):
 out=[]
 for field in ('PublicRemarks','PrivateRemarks'):
  text=str(raw.get(field) or '')
  for name,pat in PATTERNS.items():
   matches=list(re.finditer(pat,text,re.I))
   if not matches: out.append({'source_field':field,'signal_name':name,'state':'UNKNOWN','start_offset':None,'end_offset':None,'snippet':None})
   for m in matches:
    prefix=text[max(0,m.start()-35):m.start()]
    negmatch=re.search(r'\b(?:no|not|without)\s+(?:any\s+)?$',prefix,re.I)
    neg=bool(negmatch)
    start=max(0,m.start()-35)+negmatch.start() if negmatch else m.start()
    out.append({'source_field':field,'signal_name':name,'state':'ABSENT' if neg else 'PRESENT','start_offset':start,'end_offset':m.end(),'snippet':text[start:m.end()]})
 return out

def prepare(source,stored):
 variants={}
 for x in stored:
  raw=x['raw_payload'];key=mls(raw.get('ListingKey') or x.get('listing_key'))
  if key: variants.setdefault(key,{})[fingerprint(raw)]={'raw':raw,'snapshot_at':x['updated_at'],'hash':fingerprint(raw)}
 listing_groups={}
 for s in source['simplified']:
  for field in ('Prior Sale MLS','Last Sale MLS'):
   key=mls(s[field])
   if key:listing_groups.setdefault(key,set()).add(norm_apn(s['APN'])+'|'+norm_unit(s['Unit #']))
 out=[]
 originals={(norm_apn(o['APN']),norm_unit(o['Unit #'])):o for o in source['original']}
 for idx,(s,a) in enumerate(zip(source['simplified'],source['audit']),2):
  o=originals[(norm_apn(s['APN']),norm_unit(s['Unit #']))]
  assert norm_apn(s['APN'])==norm_apn(a['Normalized_APN'])==norm_apn(o['APN'])
  assert a['Simplified_Row']==idx
  item={'row':idx,'normalized_apn':norm_apn(s['APN']),'normalized_unit':norm_unit(s['Unit #']),'address':s['Address'],'source':{'simplified':s,'audit':a,'original':o},'events':[]}
  for prefix,role in [('Prior','acquisition'),('Last','later_resale')]:
   key=mls(s[prefix+' Sale MLS']);all_versions=list(variants.get(key,{}).values())
   selected=max(all_versions,key=lambda v:(len(v['raw']),v['snapshot_at'])) if all_versions else None
   reasons=[]
   if not key: reasons.append('NO_CANDIDATE')
   if key and not selected: reasons.append('MISSING_STORED_PAYLOAD')
   if a['Assignment_Status']!='matched': reasons.append('AMBIGUOUS_ASSIGNMENT')
   if s['Prior Sale Date'] and s['Last Sale Date'] and s['Last Sale Date']<s['Prior Sale Date']: reasons.append('SOURCE_TRANSACTION_ORDER_CONFLICT')
   if key and len(listing_groups[key])>1: reasons.append('LISTING_SHARED_ACROSS_IDENTITIES')
   gap=a[prefix+'_Date_Gap_Days']
   if gap is not None and int(gap)>120: reasons.append('DATE_GAP_OVER_120')
   raw=selected['raw'] if selected else {}
   if raw and norm_apn(raw.get('ParcelNumber'))!=item['normalized_apn']: reasons.append('APN_MISMATCH')
   if raw and norm_unit(raw.get('UnitNumber'))!=item['normalized_unit']: reasons.append('UNIT_REVIEW_REQUIRED')
   if raw and str(raw.get('StreetNumber') or '') != str(s['Address']).split()[0]: reasons.append('ADDRESS_NUMBER_REVIEW_REQUIRED')
   if key and key==mls(s[('Last' if prefix=='Prior' else 'Prior')+' Sale MLS']): reasons.append('SHARED_LISTING_REQUIRES_TRANSACTION_REVIEW')
   status='source_only' if not key else 'provider_error' if not selected else 'ambiguous' if reasons else 'machine_candidate'
   record_date='Prior Sale Recordng Date' if prefix=='Prior' else 'Last Sale Recording Date'
   tx={'close_date':s[prefix+' Sale Date'],'recording_date':s[record_date],'close_price':s[prefix+' Sale Amount']}
   item['events'].append({'role':role,'key':key,'decision':status,'reasons':reasons,'gap':gap,'transaction':tx,'transaction_identity':fingerprint(tx),'versions':all_versions,'selected_hash':selected['hash'] if selected else None,'signals':signals(raw),'metadata':{'structured':{f:raw.get(f) for f in STRUCTURED},'missingness':{f:raw.get(f) is None for f in STRUCTURED},'public_remarks':raw.get('PublicRemarks'),'private_signals':[q for q in signals(raw) if q['source_field']=='PrivateRemarks'],'point_in_time_verified':False,'photo_era_verified':False,'role':role},'audit':{k:v for k,v in a.items() if k.startswith(prefix+'_') or k in ('Assignment_Status','Reason','Candidate_Count')},'suggested_overall':'TARGET' if role=='acquisition' and key else 'NOT_TARGET' if key else 'UNKNOWN'})
  out.append(item)
 return out

def main():
 import argparse
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--workbook',required=True)
 parser.add_argument('--stored-evidence',nargs='+',required=True,help='JSON arrays with raw_payload and updated_at; never written to Git')
 parser.add_argument('--output',required=True,help='Private prepared JSON output')
 args=parser.parse_args();source=read_workbook(args.workbook)
 stored=[]
 for path in args.stored_evidence:stored.extend(json.loads(Path(path).read_text()))
 rows=prepare(source,stored);Path(args.output).write_text(canonical(rows))
 print(canonical({'source_sha256':source['sha256'],'properties':len(rows),'role_candidates':Counter(e['role'] for r in rows for e in r['events'] if e['key']),'decisions':Counter(e['decision'] for r in rows for e in r['events'])}))
if __name__=='__main__':main()
