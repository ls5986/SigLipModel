"""Sale-specific MLS text evidence; never infer target status or use asking price."""
import re
from collections import Counter

REMARK_KEYS = ('PublicRemarks', 'PublicRemarksAdditional', 'InternetRemarks', 'MarketingRemarks')
SYNTHETIC = re.compile(r'\b(?:AI|virtual(?:ly)?[\s-]+stag(?:ed|ing)|digitally[\s-]+stag(?:ed|ing)|AI[\s-]+(?:generated|enhanced|staged|rendered)|artificial intelligence|computer[\s-]+generated|digital(?:ly)?[\s-]+(?:enhanced|render(?:ed|ing))|render(?:ed|ing)[\s-]+(?:image|photo)s?)\b', re.I)
STOP = set('a an and are as at be been by can for from has have in is it its of on or our the this to was were with you your'.split())

def remarks(listing):
    return '\n'.join(str(listing[k]).strip() for k in REMARK_KEYS if listing.get(k))

def synthetic_evidence(text):
    matches = sorted({m.group(0).casefold() for m in SYNTHETIC.finditer(text or '')})
    return {'excluded':bool(matches), 'matches':matches,
            'reason':'MLS discloses AI imagery or virtual staging; exclude photos until original images are identified' if matches else None}

def image_evidence(listing, description=''):
    return synthetic_evidence(remarks(listing)+'\n'+description)

def phrase_report(listings, minimum=2):
    """Count distinct listings, not duplicated workbook rows or repeated occurrences."""
    counts = Counter(); seen = set(); disclosures = []; total = 0
    for listing in listings:
        key = str(listing.get('ListingKey') or listing.get('id') or '')
        if not key or key in seen: continue
        seen.add(key); total += 1
        text = remarks(listing); evidence = synthetic_evidence(text)
        if evidence['excluded']: disclosures.append({'listing_key':key,'matches':evidence['matches']})
        # Dollar amounts and numeric features cannot become keyword signals.
        text = re.sub(r'[^.!?;\n]*\b(?:price|pricing|priced|asking|cash|financing|mortgage|profit)\b[^.!?;\n]*', ' ', text.casefold())
        text = re.sub(r'\$[\d,.]+(?:\s*(?:million|[mk]))?', ' ', text.casefold())
        words = re.findall(r'\b[a-z]{3,}\b', text)
        phrases = {word for word in words if word not in STOP}
        for size in (2,3):
            phrases.update(' '.join(words[i:i+size]) for i in range(len(words)-size+1)
                           if all(w not in STOP for w in words[i:i+size]))
        counts.update(phrases)
    return {'listings':total,'synthetic_disclosures':disclosures,
            'phrases':[{'phrase':p,'listings':n} for p,n in counts.most_common() if n>=minimum],
            'notice':'Descriptive trends among known targets; these are not validated predictive signals.'}
