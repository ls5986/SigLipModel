"""Lossless v2 event materialization using the existing physical-example primary key.

A dataset item belongs to one imported example. Its immutable JSON snapshot can
contain both acquisition and after events; training expands them back into
separate rows. No new source examples, schema migration or permission changes.
"""
from collections import defaultdict
from copy import deepcopy

FORMAT = 'actvision-event-items-v1'
IDENTITY_FIELDS = {'example_id', 'source_group_id', 'split'}


def row_order(row):
    return (row['split_group_id'], row.get('event_role', 'acquisition'), row['property_id'])


def pack_rows(rows):
    """Return dataset_groups and unique physical dataset_items, preserving every event."""
    by_example = defaultdict(list)
    groups = {}
    split_groups = {}
    seen_events = set()
    for row in rows:
        if row.get('event_role') not in {'acquisition', 'after'}:
            raise ValueError('Dataset event role is required')
        if row.get('split') not in {'train', 'validation', 'test'}:
            raise ValueError('Invalid dataset split')
        for key, mapping in (('source_group_id', groups), ('split_group_id', split_groups)):
            previous = mapping.setdefault(row[key], row['split'])
            if previous != row['split']:
                raise ValueError('Physical property events leaked across dataset splits')
        event = (row['split_group_id'], row['event_role'])
        if event in seen_events:
            raise ValueError('Duplicate canonical property event')
        seen_events.add(event)
        by_example[row['example_id']].append(row)

    items = []
    for example_id, events in sorted(by_example.items()):
        if len({r['source_group_id'] for r in events}) != 1:
            raise ValueError('One example cannot belong to multiple source groups')
        events = sorted(events, key=row_order)
        snapshots = [{k: deepcopy(v) for k, v in row.items() if k not in IDENTITY_FIELDS}
                     for row in events]
        hashes = [photo['sha256'] for row in events for photo in row['photos']]
        items.append({
            'group_id': events[0]['source_group_id'], 'example_id': example_id,
            'label_snapshot': {'format': FORMAT, 'events': snapshots},
            'photo_hashes': hashes, 'source_review_ids': [],
        })
    return ([{'group_id': group_id, 'split': split} for group_id, split in sorted(groups.items())], items)


def unpack_rows(records, materialization=None):
    """Reconstruct exact manifest rows; identity is taken from relational columns."""
    if materialization not in {None, FORMAT}:
        raise ValueError('Unsupported frozen dataset materialization')
    values = []
    for record in records:
        snapshot = record['label_snapshot']
        if materialization == FORMAT:
            if snapshot.get('format') != FORMAT or set(snapshot) != {'format', 'events'}:
                raise ValueError('Frozen event snapshot format mismatch')
            events = snapshot.get('events')
            if not isinstance(events, list) or not 1 <= len(events) <= 2:
                raise ValueError('Frozen example must contain one or two events')
        else:
            # Historical flat materialization remains readable without changing
            # its original hash or inventing a missing event role.
            if 'format' in snapshot or 'events' in snapshot:
                raise ValueError('Unexpected event format in legacy dataset')
            events = [snapshot]
        expected_hashes = []
        for event in events:
            if IDENTITY_FIELDS.intersection(event):
                raise ValueError('Snapshot cannot override relational identity')
            expected_hashes.extend(photo['sha256'] for photo in event['photos'])
            values.append({
                **deepcopy(event), 'split': record['split'],
                'example_id': str(record['example_id']),
                'source_group_id': str(record['group_id']),
            })
        if expected_hashes != list(record['photo_hashes']):
            raise ValueError('Frozen photo manifest mismatch')
    if materialization == FORMAT:
        # Reuses the exact split/identity/duplicate checks applied before storage.
        pack_rows(values)
    return sorted(values, key=row_order)
