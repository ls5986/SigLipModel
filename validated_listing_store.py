"""Read accepted MLS-validation aliases through the normal Studio property API.

Validation decisions remain authoritative. This adapter never writes a listing
key, changes an approval, or relaxes the existing source/era checks.
"""
from copy import deepcopy
from datetime import datetime, timezone
import re

from cloud_store import SupabaseStore


ACCEPTED_SQL = """
    SELECT e.id,e.listing_key,e.source_rows,e.source_snapshot,e.group_id,
           g.protected_test,v.payload AS accepted_validation,v.revision AS validation_revision
    FROM acq_training.examples e
    JOIN acq_training.property_groups g
      ON (g.workspace_id,g.id)=(e.workspace_id,e.group_id)
    JOIN acq_training.studio_state v
      ON v.workspace_id=e.workspace_id AND v.kind='document'
     AND v.item_id='mls-validation:'||e.id::text
    WHERE e.workspace_id=%s AND e.listing_key IS NULL
      AND nullif(e.source_snapshot->'event_map'->>'acquisition_listing_key','') IS NULL
      AND coalesce(e.source_snapshot->'event_map'->>'recovery_status','') <> 'acquisition_mls_unavailable'
      AND v.payload->>'decision'='confirmed'
      AND v.payload->>'certified_for_training'='true'
      AND v.payload->>'selected_listing_key'=%s
    ORDER BY e.id
"""


def accepted_projection(row, identifier):
    """Project only the exact, still-confirmed stored candidate, never a best guess."""
    validation = row.get('accepted_validation') or {}
    if (row.get('listing_key') is not None
            or validation.get('decision') != 'confirmed'
            or validation.get('certified_for_training') is not True
            or validation.get('selected_listing_key') != identifier):
        return None
    snapshot = row.get('source_snapshot') or {}
    event = snapshot.get('event_map') or {}
    if event.get('acquisition_listing_key') or event.get('recovery_status') == 'acquisition_mls_unavailable':
        return None
    selected = [c for c in snapshot.get('mls_candidates', [])
                if str((c.get('listing') or {}).get('ListingKey') or '') == identifier]
    if not selected:
        return None
    if any(c != selected[0] for c in selected[1:]):
        raise ValueError('Accepted listing has conflicting saved candidates')
    projected = deepcopy(dict(row))
    projected['listing_key'] = identifier
    return projected


def accepted_media(row, media, identifier):
    """Expose only retained bytes bound to this accepted listing, not another era."""
    if (media.get('listing_key') != identifier
            or media.get('status') not in {'complete', 'sampled'}):
        return []
    photos = []
    for image in media.get('images', []):
        context = image.get('context_evidence') or {}
        if context.get('event_role', 'acquisition') != 'acquisition':
            continue
        if context.get('validation_listing_key', identifier) != identifier:
            continue
        if image.get('revoked_at'):
            continue
        expires = image.get('retention_until')
        if expires:
            try:
                until = datetime.fromisoformat(str(expires).replace('Z', '+00:00'))
                if until.tzinfo is None or until <= datetime.now(timezone.utc):
                    continue
            except ValueError:
                continue
        key = str(image.get('provider_media_key') or '')
        if (not key or not re.fullmatch(r'[a-f0-9]{64}', str(image.get('image_sha256') or ''))
                or not image.get('storage_bucket') or not image.get('storage_object_key')):
            continue
        photos.append({
            **image, 'id': image.get('id'),
            'image_id': 'validation:' + str(row['id']) + ':' + key,
            'group_id': row['group_id'], 'protected_test': bool(row['protected_test']),
            'context': image.get('context') or 'unknown', 'context_evidence': context,
        })
    return photos


class ValidatedListingStore(SupabaseStore):
    def _accepted_examples(self, db, identifier):
        rows = db.execute(ACCEPTED_SQL, (self.workspace, identifier)).fetchall()
        return [projected for row in rows
                if (projected := accepted_projection(row, identifier)) is not None]

    def _examples(self, db, identifier):
        try:
            return super()._examples(db, identifier)
        except ValueError as exc:
            # Do not hide conflicts, storage errors, or other validation failures.
            if str(exc) != 'Unknown property':
                raise
            rows = self._accepted_examples(db, identifier)
            if not rows:
                raise
            return rows

    def _photos(self, db, identifier):
        photos = super()._photos(db, identifier)
        if photos:
            return photos
        for row in self._accepted_examples(db, identifier):
            media = self.database.state(db, 'document', 'mls-validation-media:' + str(row['id'])) or {}
            photos.extend(accepted_media(row, media, identifier))
        # The existing property renderer, review precedence, image-byte hash checks,
        # and acquisition/era policy are deliberately reused unchanged.
        return sorted(photos, key=lambda p: p['image_id'])
