"""Recover acquisition-listing photos for trusted before/after event maps."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass

import httpx

from backfill_validation_media import (
    BUCKET,
    download_image,
    object_key,
    storage_client,
    storage_objects,
    storage_upload,
    storage_delete,
)
from studio_data import now

WORKER_KEY = "acquisition-event-recovery-worker"


@dataclass(frozen=True)
class MediaRecord:
    url: str
    order: int | None
    media_key: str | None
    category: str | None
    short_description: str | None
    long_description: str | None
    image_of: str | None
    media_type: str | None
    modification_timestamp: str | None


@dataclass(frozen=True)
class MediaRecords:
    records: list[MediaRecord]
    truncated: bool


class HostedTrestleClient:
    def __init__(self):
        client_id = os.environ.get("TRESTLE_CLIENT_ID", "")
        secret = os.environ.get("TRESTLE_CLIENT_SECRET", "")
        if not client_id or not secret:
            raise ValueError("Cotality credentials are not configured")
        self.client_id = client_id
        self.secret = secret
        self.http = httpx.AsyncClient(
            base_url="https://api.cotality.com",
            timeout=45,
            follow_redirects=False,
            trust_env=False,
        )
        self._token = None

    async def close(self):
        await self.http.aclose()

    async def _access_token(self):
        if self._token:
            return self._token
        response = await self.http.post(
            "/trestle/oidc/connect/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.secret,
                "grant_type": "client_credentials",
                "scope": "api",
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Cotality authentication omitted access token")
        self._token = token
        return token

    async def media_records(self, listing_key, *, max_images=24):
        token = await self._access_token()
        escaped = str(listing_key).replace("'", "''")
        fields = [
            "MediaURL", "Order", "MediaKey", "MediaCategory",
            "ShortDescription", "LongDescription", "ImageOf", "MediaType",
            "ModificationTimestamp",
        ]
        response = await self.http.get(
            "/trestle/odata/Media",
            params={
                "$select": ",".join(fields),
                "$filter": f"ResourceRecordKey eq '{escaped}'",
                "$top": max_images,
                "$orderby": "Order",
            },
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        values = payload.get("value")
        if not isinstance(values, list):
            raise ValueError("Cotality media response is invalid")
        records = []
        for row in values:
            url = row.get("MediaURL")
            if not isinstance(url, str) or not url:
                continue
            records.append(MediaRecord(
                url=url,
                order=row.get("Order") if isinstance(row.get("Order"), int) else None,
                media_key=str(row.get("MediaKey")) if row.get("MediaKey") is not None else None,
                category=row.get("MediaCategory"),
                short_description=row.get("ShortDescription"),
                long_description=row.get("LongDescription"),
                image_of=row.get("ImageOf"),
                media_type=row.get("MediaType"),
                modification_timestamp=row.get("ModificationTimestamp"),
            ))
        return MediaRecords(records[:max_images], bool(payload.get("@odata.nextLink")))


def heartbeat(store, status, detail=None):
    current = store.document(WORKER_KEY) or {}
    return store.save_document(
        WORKER_KEY,
        {
            "status": status,
            "at": now(),
            "detail": detail,
            "deployed_commit": os.environ.get("RENDER_GIT_COMMIT", "unknown")[:40],
        },
        current.get("revision", 0),
    )


def pending(store, limit=1):
    with store.database.connect() as db:
        rows = db.execute("""
          SELECT e.id,e.listing_key,e.source_snapshot,
                 v.payload AS validation
          FROM acq_training.examples e
          JOIN acq_training.studio_state v
            ON v.workspace_id=e.workspace_id
           AND v.kind='document'
           AND v.item_id='mls-validation:'||e.id::text
          LEFT JOIN acq_training.studio_state m
            ON m.workspace_id=e.workspace_id
           AND m.kind='document'
           AND m.item_id='mls-validation-media:'||e.id::text
          WHERE e.workspace_id=%s
            AND e.source_snapshot->'event_map'->>'recovery_status'='mapped'
            AND e.source_snapshot->'event_map'->>'acquisition_listing_key'<>e.listing_key
            AND v.payload->>'certified_for_training'='true'
            AND NOT (
              m.payload->>'status'='complete'
              AND coalesce((m.payload->>'reported_photo_count')::int,0)=0
            )
            AND coalesce(jsonb_array_length(m.payload->'images'),0)=0
            AND NOT EXISTS (
              SELECT 1 FROM acq_training.photos p
              WHERE (p.workspace_id,p.example_id)=(e.workspace_id,e.id)
                AND p.revoked_at IS NULL
                AND (p.retention_until IS NULL OR p.retention_until>now())
                AND p.context_evidence->>'event_role'='acquisition'
            )
          ORDER BY e.id
          LIMIT %s
        """, (store.workspace, limit)).fetchall()
    result = []
    for row in rows:
        event_map = row["source_snapshot"].get("event_map") or {}
        selected = next((
            candidate for candidate in row["source_snapshot"].get("mls_candidates", [])
            if str(candidate.get("listing", {}).get("ListingKey"))
               == str(event_map.get("acquisition_listing_key"))
        ), None)
        if not selected:
            continue
        listing = selected.get("listing", {})
        result.append({
            "id": str(row["id"]),
            "listing_key": str(event_map["acquisition_listing_key"]),
            "listing_id": event_map.get("acquisition_listing_id"),
            "reported_photos": int(listing.get("PhotosCount") or 0),
        })
    return result


async def recover_one(store, row):
    trestle = HostedTrestleClient()
    project = storage = None
    try:
        media = await trestle.media_records(row["listing_key"], max_images=24)
        if not media.records and row["reported_photos"] > 0:
            raise ValueError("Provider returned no acquisition media")
        project, storage = storage_client()
        saved = {}
        failures = 0
        for index, record in enumerate(media.records):
            try:
                blob = await download_image(trestle, record.url)
                import hashlib
                digest = hashlib.sha256(blob).hexdigest()
                key = object_key(row["id"], digest)
                storage_upload(storage, project, key, blob)
                provider_key = record.media_key or f"order-{record.order or index + 1}"
                saved[(provider_key, digest)] = {
                    "provider_media_key": provider_key,
                    "image_sha256": digest,
                    "storage_bucket": BUCKET,
                    "storage_object_key": key,
                    "provider_modified_at": record.modification_timestamp,
                    "context": "unknown",
                    "context_evidence": {
                        "event_role": "acquisition",
                        "event_listing_key": row["listing_key"],
                        "event_mapping_policy": "exact-apn-chronology-v1",
                        "validation_listing_key": row["listing_key"],
                        "validation_listing_id": row["listing_id"],
                        "provider_metadata": {
                            "Order": record.order,
                            "MediaKey": provider_key,
                            "MediaCategory": record.category,
                            "ShortDescription": record.short_description,
                            "LongDescription": record.long_description,
                            "ImageOf": record.image_of,
                            "MediaType": record.media_type,
                            "ModificationTimestamp": record.modification_timestamp,
                        },
                    },
                }
            except (ValueError, RuntimeError, httpx.HTTPError):
                failures += 1
        if row["reported_photos"] > 0 and not saved:
            raise ValueError("No usable acquisition photos were recovered")
        key = "mls-validation-media:" + row["id"]
        current = store.document(key) or {}
        complete = (
            not media.truncated and failures == 0
            and (row["reported_photos"] == 0 or len(saved) >= row["reported_photos"])
        )
        store.save_document(key, {
            "example_id": row["id"],
            "listing_key": row["listing_key"],
            "listing_id": row["listing_id"],
            "status": "complete" if complete else "sampled",
            "provider_media_count": len(saved),
            "reported_photo_count": row["reported_photos"],
            "truncated": media.truncated,
            "max_images": 24,
            "provider_records_examined": len(media.records),
            "image_failures": failures,
            "event_role": "acquisition",
            "images": sorted(saved.values(), key=lambda image: (
                image.get("context_evidence", {}).get("provider_metadata", {}).get("Order")
                or 2147483647,
                image.get("provider_media_key", ""),
            )),
            "at": now(),
        }, current.get("revision", 0))
        prefix = "validation-photos/" + row["id"]
        keep = {image["storage_object_key"] for image in saved.values()}
        obsolete = [name for name in storage_objects(storage, project, prefix) if name not in keep]
        storage_delete(storage, project, obsolete)
        return {
            "example_id": row["id"],
            "listing_key": row["listing_key"],
            "photos": len(saved),
            "status": "complete" if complete else "sampled",
            "failures": failures,
        }
    finally:
        await trestle.close()
        if storage:
            storage.close()


def poll(store):
    rows = pending(store, 1)
    if not rows:
        heartbeat(store, "ready", {"remaining": 0})
        return False
    row = rows[0]
    heartbeat(store, "running", {"example_id": row["id"], "listing_key": row["listing_key"]})
    try:
        result = asyncio.run(recover_one(store, row))
        remaining = len(pending(store, 100))
        heartbeat(store, "ready", {"latest": result, "remaining": remaining})
        return True
    except Exception as exc:
        heartbeat(store, "failed", {
            "example_id": row["id"],
            "listing_key": row["listing_key"],
            "error_code": type(exc).__name__,
        })
        raise
