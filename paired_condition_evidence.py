"""Versioned, deterministic evidence policy; never changes human labels or model artifacts."""
import re
from copy import deepcopy

VERSION = 'remarks-condition-evidence-v3'
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
                if re.search(r'\b(?:no|not|without|never)\s+(?:any\s+|a\s+|longer\s+|considered\s+(?:a\s+)?)?$',prefix,re.I):continue
                if re.search(r'\b(?:could|can|will|would|may|might|to)\s+(?:easily\s+)?(?:be|become)\s*$|\bplans? (?:call for|to be)\s+(?:a\s+)?$',prefix,re.I):continue
                signal_category=category
                room_before=re.search(r'\b(?:kitchen|bath(?:room)?s?|roof|pool|garage|ADU)\s+(?:(?:is|are|was|were|has been|have been)\s+)?$',prefix,re.I)
                room_after=re.search(ROOM,suffix,re.I)
                scope='room' if room_before or room_after or re.search(r'\b(?:kitchen|bath(?:room)?s?)\b',m.group(),re.I) else 'whole_property'
                if category=='fully_updated' and (room_before or room_after):signal_category='room_updated'
                weak=bool(re.search(r'TLC|potential|upside|personalize|diamond|room for updates|ready for (?:you to )?updat',m.group(),re.I))
                if category=='dated' and weak:signal_category='opportunity_language'
                confidence='low' if weak else 'high'

                quote=text[max(0,m.start()-25):min(len(text),m.end()+65)].strip()
                out.append({'source_field':source,'category':signal_category,'scope':scope,'confidence':confidence,'phrase':m.group(),'snippet':quote,'start_offset':m.start(),'end_offset':m.end()})
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
    weak=[x for x in evidence if x['category']=='opportunity_language']
    room_updates=[x for x in evidence if x['category']=='room_updated']
    mixed=bool(dated and (updated or room_updates))
    conflict=bool(dated and updated and not re.search(r'\b(?:except|but|however|partially|partly)\b',str(public)+' '+str(private),re.I))
    reason=None
    unknown=lambda:{'decision':'INSUFFICIENT_EVIDENCE','strength':None,'affinity':None}
    if conflict:
        meta=unknown();reason='Remarks contain conflicting whole-home renovation and dated-condition evidence.'
    elif dated:
        meta={'decision':'TARGET','strength':50,'affinity':None}
        reason='Explicit dated or repair evidence qualifies; an updated room does not negate dated rooms.'
    elif updated:
        meta={'decision':'NOT_TARGET','strength':None,'affinity':None}
        reason='Explicit whole-home renovation evidence; room-only upgrades do not establish this.'
    else:
        text=result.get('text') or {};structured=result.get('structured') or {}
        meta=deepcopy(text) if text.get('decision')=='NOT_TARGET' and structured.get('decision')=='INSUFFICIENT_EVIDENCE' else deepcopy(result.get('metadata') or unknown())
        if weak and meta.get('decision')=='TARGET':
            meta=unknown();reason='Potential, investor or personalization language alone does not establish dated condition.'
    result['metadata']=meta
    image=result.get('image') or {}
    image_decision=image.get('decision');meta_decision=meta.get('decision')
    disagreement=image_decision in {'TARGET','NOT_TARGET'} and meta_decision in {'TARGET','NOT_TARGET'} and image_decision!=meta_decision
    targets=[x for x in (image,meta) if x.get('decision')=='TARGET']
    if conflict or disagreement:
        overall=unknown()
        reason=(reason or '')+' Photos and remarks disagree or contain conflicting condition evidence; review required.'
    elif image_decision=='NOT_TARGET' and meta_decision=='INSUFFICIENT_EVIDENCE' and weak and not dated:
        overall={'decision':'NOT_TARGET','strength':None,'affinity':None}
        reason='Updated photos are not overridden by vague opportunity language.'
    elif image_decision=='TARGET' and meta_decision=='TARGET':
        overall={'decision':'TARGET','strength':max(x.get('strength') or 50 for x in targets),'affinity':None}
    elif meta_decision=='TARGET' and dated:
        overall=deepcopy(meta)
    elif image_decision=='TARGET' and (image.get('strength') or 0)>=70 and result.get('usable_photo_count',0)>=2:
        overall=deepcopy(image)
    elif image_decision=='NOT_TARGET' and meta_decision=='NOT_TARGET':
        overall={'decision':'NOT_TARGET','strength':None,'affinity':None}
    else:overall=unknown()
    result['overall']=overall
    result['learned_prediction']=original
    result['evidence_policy']={'version':VERSION,'reason':reason,'signals':evidence,
        'condition_signal':'mixed_condition' if mixed else 'dated_or_rehab_needed' if dated else 'whole_property_updated' if updated else 'unknown',
        'conflict':conflict or disagreement,'needs_review':conflict or disagreement,
        'automated':True,'not_human_review':True}
    result['disagreement']=disagreement
    return result
