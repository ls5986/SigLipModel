"""Versioned, deterministic evidence policy; never changes human labels or model artifacts."""
import re
from copy import deepcopy

VERSION = 'remarks-condition-evidence-v2'
DATED = r'\b(?:dated|fixer(?:[ -]upper)?|TLC|deferred maintenance|original (?:appliances|kitchen|bath(?:room)?s?|finishes|interior|condition)|needs? (?:some |a little |major )?(?:updating|renovation|work|repairs)|ready for (?:you to )?updat\w*|room for updates|partially updated|partly renovated|renovate to your taste|(?:kitchen|bath(?:room)?s?)\s+(?:needs?|requires?)\s+(?:a\s+)?(?:(?:total|complete|major)\s+)?(?:overhaul|renovation|remodeling)|ready for (?:remodeling|renovation)|(?:great )?potential to update(?: and personalize)?|(?:solid )?upside for updating|personalize and add value|diamond in the rough)\b'
WHOLE_UPDATED = r'\b(?:(?:fully|completely|totally|extensively|newly|recently) (?:updated|renovated|remodeled)|(?:updated|renovated|remodeled) throughout|(?:beautifully|meticulously) remodeled (?:home|residence|condo)|(?:remodeled|renovated) (?:inside and out|from top to bottom)|turn[ -]?key (?:quality )?throughout|every major update already done)\b'
ROOM = r'^\s*(?:(?:the|a|an|chef.s)\s+)*(?:kitchen|bath(?:room)?s?|pool|roof|garage|ADU|guest|patio|landscap\w*)\b'

def extract(public, private):
    out=[]
    for source,text in [('PublicRemarks',public),('PrivateRemarks',private)]:
        text=str(text or '')
        # Private access/contacts and verification boilerplate do not establish condition.
        for category,pattern in [('dated',DATED),('fully_updated',WHOLE_UPDATED)]:
            for m in re.finditer(pattern,text,re.I):
                prefix=text[max(0,m.start()-55):m.start()]
                suffix=text[m.end():m.end()+55]
                if re.search(r'\b(?:no|not|without|never)\s+(?:any\s+|a\s+)?$',prefix,re.I):continue
                if re.search(r'\b(?:could be|can be|will be|to be|potential to be|plans? to be)\s*$',prefix,re.I):continue
                if category=='fully_updated' and re.search(ROOM,suffix,re.I):continue
                quote=text[max(0,m.start()-25):min(len(text),m.end()+65)].strip()
                out.append({'source_field':source,'category':category,'phrase':m.group(),'snippet':quote,'start_offset':m.start(),'end_offset':m.end()})
    seen=set();unique=[]
    for x in out:
        key=(x['source_field'],x['category'],x['phrase'].casefold())
        if key not in seen:unique.append(x);seen.add(key)
    return unique

def apply_policy(prediction,public='',private=''):
    if not prediction or prediction.get('status')!='completed':return prediction
    result=deepcopy(prediction)
    # Idempotent projection retains the original learned results for comparison.
    original=result.pop('learned_prediction',None) or {k:deepcopy(result.get(k)) for k in ('image','text','structured','metadata','overall')}
    for k,v in original.items():result[k]=deepcopy(v)
    evidence=extract(public,private)
    dated=[x for x in evidence if x['category']=='dated']
    updated=[x for x in evidence if x['category']=='fully_updated']
    reason=None
    if dated:
        meta={'decision':'TARGET','strength':50,'affinity':None}
        reason='Explicit dated / updating evidence qualifies metadata; partial upgrades do not negate it.'
    elif updated:
        meta={'decision':'NOT_TARGET','strength':None,'affinity':None}
        reason='Explicit whole-home renovation evidence takes precedence over indirect structured similarities.'
    else:
        text=result.get('text') or {};structured=result.get('structured') or {}
        if text.get('decision')=='NOT_TARGET' and structured.get('decision')=='INSUFFICIENT_EVIDENCE':
            meta=deepcopy(text);reason='Decisive remarks prediction is not blocked by uncertain structured fields.'
        else:meta=result.get('metadata') or {'decision':'INSUFFICIENT_EVIDENCE','strength':None,'affinity':None}
    result['metadata']=meta
    image=result.get('image') or {}
    targets=[x for x in (image,meta) if x.get('decision')=='TARGET']
    if targets:overall={'decision':'TARGET','strength':max(x.get('strength') or 50 for x in targets),'affinity':None}
    elif image.get('decision')=='NOT_TARGET' and meta.get('decision')=='NOT_TARGET':overall={'decision':'NOT_TARGET','strength':None,'affinity':None}
    else:overall={'decision':'INSUFFICIENT_EVIDENCE','strength':None,'affinity':None}
    result['overall']=overall
    result['learned_prediction']=original
    result['evidence_policy']={'version':VERSION,'reason':reason,'signals':evidence,'automated':True,'not_human_review':True}
    result['disagreement']=image.get('decision') in {'TARGET','NOT_TARGET'} and meta['decision'] in {'TARGET','NOT_TARGET'} and image['decision']!=meta['decision']
    return result
