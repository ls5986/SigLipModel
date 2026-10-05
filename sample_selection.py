"""Review sample selections, separate from labels, datasets and protected splits."""
from studio_data import now
from urllib.parse import quote

KEY = 'studio-review-samples-v1'

def listing(store):
    record = store.document(KEY) or {}
    return {'revision':record.get('revision', 0), 'items':record.get('items', []),
            'notice':'Review selection only. Adding a sample neither approves labels nor changes training/test membership.'}

def change(studio, payload, reviewer):
    identifier = payload.get('id')
    if not isinstance(identifier, str) or not identifier or len(identifier) > 200:
        raise ValueError('Choose an existing listing from the MLS ID search')
    if payload.get('action') not in {'add', 'remove'}:
        raise ValueError('Unknown sample action')
    revision = payload.get('expected_revision')
    if type(revision) is not int or revision < 0:
        raise ValueError('Sample revision is required')
    record = listing(studio.store)
    if revision != record['revision']:
        raise RuntimeError('Sample list changed; reload before editing')
    items = list(record['items'])
    if payload['action'] == 'add':
        # Read actual retained evidence. Never create a property or certify its era.
        detail = studio.get('/api/studio/v2/property?id=' + quote(identifier,safe=''))
        prop = detail['property']
        if not any(i['id'] == identifier for i in items):
            if len(items) >= 50:
                raise ValueError('Keep this review sample list to 50 properties or fewer')
            items.append({'id':identifier,'address':prop.get('address') or identifier,'added_by':reviewer,'at':now()})
    else:
        items = [i for i in items if i['id'] != identifier]
    studio.store.save_document(KEY, {'items':items}, revision)
    return listing(studio.store)
