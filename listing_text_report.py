"""Report recurring MLS phrases; confirmed sale matches only, one vote per physical house."""
import json
import config
from acquisition_policy import annotate_first_sales, supports_prior
from cloud_runtime import from_env
from listing_text import phrase_report


def report(store):
    with store.database.connect() as db:
        rows = db.execute('''SELECT id,listing_key,group_id,source_snapshot FROM acq_training.examples
            WHERE workspace_id=%s AND listing_key IS NOT NULL ORDER BY id''',(store.workspace,)).fetchall()
    annotate_first_sales(rows)
    listings = []; seen = set(); unresolved = 0
    for row in rows:
        candidate = store._selected(row)
        if not supports_prior(candidate,row['source_snapshot'].get('spreadsheet'),row['source_snapshot'].get('mls_candidates')):
            unresolved += 1; continue
        group = str(row['group_id'])
        if group in seen: continue
        seen.add(group); listings.append(candidate.get('listing',{}))
    result = phrase_report(listings)
    result['unmatched_source_records'] = unresolved
    return result


if __name__=='__main__':
    store = from_env().get_studio().store
    result = report(store)
    current = store.document('mls-phrase-report') or {}
    store.save_document('mls-phrase-report',result,current.get('revision',0))
    print(json.dumps(result,indent=2))
