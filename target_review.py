"""A frozen list of properties with plain, independent review columns."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

DECISIONS = {'TARGET', 'NOT_TARGET', 'INSUFFICIENT_EVIDENCE'}
TABLE = 'acq_training.property_target_review'


def validate_answer(payload):
    if not isinstance(payload, dict) or set(payload) - {'group_id','expected_revision','overall','image','metadata','notes'}:
        raise ValueError('Invalid review fields')
    group = str(UUID(str(payload.get('group_id'))))
    revision = payload.get('expected_revision')
    if type(revision) is not int or revision < 0:
        raise ValueError('Expected revision required')
    if payload.get('overall') not in {'TARGET','NOT_TARGET'}:
        raise ValueError('Choose the overall reference')
    result = {'group_id':group,'overall':payload['overall']}
    for axis in ('image','metadata'):
        answer = payload.get(axis)
        if not isinstance(answer,dict) or set(answer) != {'decision','strength'}:
            raise ValueError('Make both the photo and metadata decisions')
        decision,strength = answer['decision'],answer['strength']
        if decision not in DECISIONS:
            raise ValueError('Invalid decision')
        if decision=='TARGET':
            if type(strength) is not int or not 50<=strength<=100:
                raise ValueError('TARGET strength must be 50 through 100')
        elif strength is not None:
            raise ValueError('Only TARGET has a target-strength score')
        result[axis] = {'decision':decision,'strength':strength}
    notes = payload.get('notes','')
    if not isinstance(notes,str) or len(notes)>4000:
        raise ValueError('Notes must be at most 4000 characters')
    result['notes']=notes
    return result,revision


def present(row, detail=False):
    raw = row.get('odata_json') or {}
    result={'group_id':str(row['group_id']), 'listing_key':row['listing_key'],
        'listing_id':raw.get('ListingId') or row['listing_key'],
        'address':raw.get('UnparsedAddress') or ' '.join(str(raw.get(k) or '')
            for k in ('StreetNumber','StreetName','StreetSuffix','City')).strip() or row['listing_key'],
        'review':{'revision':row['revision'],'overall':row['overall_decision'],
            'origin':'HUMAN_REVIEWED' if row['reviewed_at'] else 'IMPORTED_TARGET',
            'image':{'decision':row['image_decision'],'strength':row['image_strength']} if row['image_decision'] else None,
            'metadata':{'decision':row['metadata_decision'],'strength':row['metadata_strength']} if row['metadata_decision'] else None,
            'notes':row['notes'],'reviewed_at':str(row['reviewed_at']) if row['reviewed_at'] else None}}
    if detail:
        result.update(event=row['event_snapshot'],odata_json=raw,evidence_identity=row['evidence_identity'],
            source_note='Stored acquisition listing JSON, captured when this review list was created. Frozen event text/facts are shown separately if different.')
    return result


class TargetReview:
    def __init__(self,store):
        self.store,self.database=store,store.database

    def queue(self):
        with self.database.connect() as db:
            rows=db.execute('SELECT * FROM '+TABLE+' WHERE workspace_id=%s ORDER BY group_id',
                (self.database.workspace,)).fetchall()
        if len(rows)!=415:
            raise ValueError('The 415-property review list has not been provisioned in this workspace')
        items=[present(r) for r in rows]
        completed=sum(bool(r['reviewed_at']) for r in rows)
        return {'items':items,'total':len(items),'completed':completed,'remaining':len(items)-completed}

    def _row(self,db,group):
        row=db.execute('SELECT * FROM '+TABLE+' WHERE workspace_id=%s AND group_id=%s',
            (self.database.workspace,str(UUID(str(group))))).fetchone()
        if not row:
            raise ValueError('Property is outside this review list')
        return row

    def detail(self,group):
        with self.database.connect() as db:
            return present(self._row(db,group),True)

    def save(self,payload,reviewer):
        answer,revision=validate_answer(payload)
        with self.database.connect() as db:
            row=self._row(db,answer['group_id'])
            if not row['event_snapshot'].get('photos') and answer['image']['decision']!='INSUFFICIENT_EVIDENCE':
                raise ValueError('No frozen photos: choose insufficient visual evidence')
            result=db.execute('''UPDATE acq_training.property_target_review
                SET image_decision=%s,image_strength=%s,metadata_decision=%s,metadata_strength=%s,
                    overall_decision=%s,notes=%s,reviewed_by=%s,reviewed_at=now(),revision=revision+1
                WHERE workspace_id=%s AND group_id=%s AND revision=%s RETURNING *''',
                (answer['image']['decision'],answer['image']['strength'],answer['metadata']['decision'],
                 answer['metadata']['strength'],answer['overall'],answer['notes'],reviewer,
                 self.database.workspace,answer['group_id'],revision)).fetchone()
            if not result:
                raise RuntimeError('This property changed in another window. Reload before saving.')
            # Reuse the existing append-only history for review revisions.
            self.database.save(db,'document','simple-target-review:'+answer['group_id'],revision,
                {**answer,'reviewer':reviewer,'evidence_identity':row['evidence_identity'],
                 'at':datetime.now(timezone.utc).isoformat(),'origin':'HUMAN_REVIEWED'})
        return present(result)['review']

    def image(self,group,digest):
        with self.database.connect() as db:
            row=self._row(db,group)
            photo=next((p for p in row['event_snapshot'].get('photos',[]) if p.get('sha256')==digest),None)
            if not photo:
                raise ValueError('Photo is outside the selected acquisition listing')
            blocked=db.execute('''SELECT 1 FROM acq_training.photos WHERE workspace_id=%s
                AND image_sha256=%s AND (revoked_at IS NOT NULL OR retention_until<=now()) LIMIT 1''',
                (self.database.workspace,digest)).fetchone()
            if blocked:
                raise ValueError('Photo access or retention has changed')
        return self.store.storage.get(photo['storage_bucket'],photo['storage_object_key'],digest)
