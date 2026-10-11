"""Recover bounded proposed-MLS photos into private validation storage."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import io
import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import httpx
from dotenv import dotenv_values
from PIL import Image, ImageOps

import config
from cloud_runtime import from_env
from cloud_store import validation_candidate

BUCKET = "acq-training-private"
MAX_SOURCE_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 40_000_000


def mls_backend():
    configured = os.environ.get("MLS_SOURCING_BACKEND")
    path = Path(configured).expanduser() if configured else (
        Path(__file__).resolve().parent.parent / "MLSSourcing" / "backend"
    )
    path = path.resolve()
    if not (path / "app" / "services" / "trestle" / "client.py").is_file():
        raise ValueError("Set MLS_SOURCING_BACKEND to the existing MLSSourcing backend folder")
    return path


def trestle_credentials():
    values = dotenv_values(mls_backend().parent / ".env")
    client_id = os.environ.get("TRESTLE_CLIENT_ID") or values.get("TRESTLE_CLIENT_ID")
    secret = os.environ.get("TRESTLE_CLIENT_SECRET") or values.get("TRESTLE_CLIENT_SECRET")
    if not client_id or not secret:
        raise ValueError("Authorized Trestle credentials are required in the local MLSSourcing .env")
    return client_id, secret


def storage_client():
    project = os.environ.get("SUPABASE_PROJECT_REF", "")
    secret = os.environ.get("STUDIO_STORAGE_SECRET", "")
    if len(project) != 20 or not secret or secret.startswith("sb_publishable_"):
        raise ValueError("Private Supabase Storage configuration is required")
    headers = {"apikey": secret}
    if not secret.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + secret
    return project, httpx.Client(headers=headers, timeout=90, follow_redirects=False,
                                 trust_env=False)


def normalize_image(blob):
    if not 0 < len(blob) <= MAX_SOURCE_BYTES:
        raise ValueError("MLS image exceeds the bounded source size")
    with Image.open(io.BytesIO(blob)) as source:
        if source.width * source.height > MAX_PIXELS:
            raise ValueError("MLS image exceeds the pixel limit")
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((3000, 3000))
        output = io.BytesIO()
        image.save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue()


def object_key(example_id, digest):
    return f"validation-photos/{example_id}/{digest}.jpg"


def stored_object_matches(http, project, key, blob):
    base = f"https://{project}.supabase.co/storage/v1"
    path = quote(key, safe="/")
    digest = hashlib.sha256()
    size = 0
    with http.stream("GET", f"{base}/object/authenticated/{BUCKET}/{path}") as stored:
        if stored.status_code in {400,404}:
            try:
                body = json.loads(stored.read())
            except (ValueError,TypeError):
                body = {}
            if stored.status_code == 404 or body.get("error") == "not_found":
                return False
        if stored.status_code != 200:
            raise RuntimeError(f"Supabase Storage verification failed with HTTP {stored.status_code}")
        for chunk in stored.iter_bytes():
            size += len(chunk)
            digest.update(chunk)
    expected = hashlib.sha256(blob).hexdigest()
    if size != len(blob) or digest.hexdigest() != expected:
        raise ValueError("Stored validation photo checksum mismatch")
    return True


def storage_upload(http, project, key, blob):
    if stored_object_matches(http, project, key, blob):
        return False
    base = f"https://{project}.supabase.co/storage/v1"
    path = quote(key, safe="/")
    response = http.post(
        f"{base}/object/{BUCKET}/{path}",
        headers={"Content-Type": "image/jpeg", "x-upsert": "false"},
        content=blob,
    )
    if response.status_code not in {200, 201}:
        if response.status_code in {400,409} and stored_object_matches(http, project, key, blob):
            return False
        raise RuntimeError(f"Supabase Storage upload failed with HTTP {response.status_code}")
    if not stored_object_matches(http, project, key, blob):
        raise RuntimeError("Supabase Storage upload was not readable after success")
    return True


def storage_delete(http, project, keys):
    if not keys:
        return
    base = f"https://{project}.supabase.co/storage/v1"
    response = http.request(
        "DELETE",
        f"{base}/object/{BUCKET}",
        json={"prefixes": keys},
    )
    if response.status_code not in {200, 204}:
        raise RuntimeError(f"Supabase Storage cleanup failed with HTTP {response.status_code}")


def storage_objects(http, project, prefix):
    base = f"https://{project}.supabase.co/storage/v1"
    response = http.post(
        f"{base}/object/list/{BUCKET}",
        json={"prefix": prefix, "limit": 1000, "offset": 0, "sortBy": {
            "column": "name", "order": "asc",
        }},
    )
    if response.status_code != 200:
        raise RuntimeError(f"Supabase Storage listing failed with HTTP {response.status_code}")
    result = []
    for item in response.json():
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name.endswith(".jpg"):
            result.append(name if name.startswith(prefix + "/") else prefix + "/" + name)
    return result


async def download_image(client, url):
    token = await client._access_token()
    redirect = None
    async with client.http.stream(
        "GET", url, headers={"Authorization": "Bearer " + token, "Accept": "image/*"}
    ) as response:
        if response.status_code in {301,302,303,307,308}:
            redirect = response.headers.get("location")
        elif response.status_code != 200:
            response.raise_for_status()
        else:
            return normalize_image(await bounded_image_bytes(response))
    if not redirect:
        raise ValueError("Trestle media redirect omitted its destination")
    destination = urljoin(url, redirect)
    parsed = urlparse(destination)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in {None,443}):
        raise ValueError("Trestle media redirect is not a safe HTTPS URL")
    addresses = await asyncio.to_thread(
        socket.getaddrinfo, parsed.hostname, 443, 0, socket.SOCK_STREAM
    )
    if not addresses or any(
        not ipaddress.ip_address(item[4][0]).is_global for item in addresses
    ):
        raise ValueError("Trestle media redirect must resolve only to public addresses")
    async with client.http.stream(
        "GET", destination, headers={"Accept": "image/*"}
    ) as response:
        if response.status_code != 200:
            response.raise_for_status()
        return normalize_image(await bounded_image_bytes(response))


async def bounded_image_bytes(response):
    size = 0
    chunks = []
    if not response.headers.get("content-type", "").lower().startswith("image/"):
        raise ValueError("Trestle media URL did not return an image")
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_SOURCE_BYTES:
            raise ValueError("MLS image exceeds the bounded source size")
        chunks.append(chunk)
    return b"".join(chunks)


def validation_examples(store, example_id=None, limit=None):
    with store.database.connect() as db:
        rows = db.execute('''SELECT e.id,e.listing_key,e.match_status,e.source_snapshot,
            (SELECT count(*) FROM acq_training.photos p WHERE
             (p.workspace_id,p.example_id)=(e.workspace_id,e.id)
             AND p.revoked_at IS NULL
             AND (p.retention_until IS NULL OR p.retention_until>now())) AS retained_photos
            FROM acq_training.examples e WHERE e.workspace_id=%s
            AND e.match_status IN ('candidate','unresolved')
            AND (%s::uuid IS NULL OR e.id=%s::uuid)
            ORDER BY CASE e.match_status WHEN 'candidate' THEN 0 ELSE 1 END,e.id''',
            (store.workspace, example_id, example_id)).fetchall()
    selected = []
    for row in rows:
        candidate = validation_candidate(dict(row))
        listing = candidate.get("listing", {})
        reported = int(listing.get("PhotosCount") or 0)
        if not listing.get("ListingKey") or reported < 1:
            continue
        selected.append({
            "id": str(row["id"]), "match_status": row["match_status"],
            "listing_key": str(listing["ListingKey"]),
            "listing_id": listing.get("ListingId"),
            "address": listing.get("UnparsedAddress"),
            "reported_photos": reported, "retained_photos": row["retained_photos"],
        })
    return selected[:limit] if limit else selected


async def run(args):
    sys.path.insert(0, str(mls_backend()))
    from app.services.trestle.client import TrestleClient

    store = from_env().get_studio().store
    rows = validation_examples(store, args.example_id, args.limit)
    client_id, secret = trestle_credentials()
    trestle = TrestleClient(client_id, secret, timeout_seconds=45)
    project = storage = None
    if not args.dry_run:
        project, storage = storage_client()
    report = {"examples": len(rows), "media_records": 0, "inserted": 0,
              "existing": 0, "failed": 0, "dry_run": args.dry_run, "items": []}
    try:
        for row in rows:
            item = {**row, "available": 0, "inserted": 0, "failed": 0}
            try:
                media = await trestle.media_records(
                    row["listing_key"], max_images=args.max_images
                )
                item["available"] = len(media.records)
                item["truncated"] = media.truncated
                report["media_records"] += len(media.records)
                if args.dry_run:
                    report["items"].append(item)
                    print(json.dumps(item), flush=True)
                    continue
                previous_media = store.document("mls-validation-media:" + row["id"]) or {}
                saved = {}
                for index, record in enumerate(media.records):
                    try:
                        blob = await download_image(trestle, record.url)
                        digest = hashlib.sha256(blob).hexdigest()
                        key = object_key(row["id"], digest)
                        uploaded = storage_upload(storage, project, key, blob)
                        if not uploaded:
                            report["existing"] += 1
                        provider_key = record.media_key or f"order-{record.order or index+1}"
                        evidence = {
                            "validation_listing_key": row["listing_key"],
                            "validation_listing_id": row["listing_id"],
                            "provider_metadata": {
                                "Order": record.order, "MediaKey": provider_key,
                                "MediaCategory": record.category,
                                "ShortDescription": record.short_description,
                                "LongDescription": record.long_description,
                                "ImageOf": record.image_of, "MediaType": record.media_type,
                                "ModificationTimestamp": record.modification_timestamp,
                            },
                        }
                        saved[(provider_key, digest)] = {
                            "provider_media_key": provider_key,
                            "image_sha256": digest,
                            "storage_bucket": BUCKET,
                            "storage_object_key": key,
                            "provider_modified_at": record.modification_timestamp,
                            "context": "unknown",
                            "context_evidence": evidence,
                        }
                        item["inserted"] += 1
                        report["inserted"] += 1
                    except (ValueError,RuntimeError,httpx.HTTPError):
                        item["image_failures"] = item.get("image_failures",0)+1
                if not saved:
                    raise ValueError("No usable provider photos were recovered")
                media_key = "mls-validation-media:" + row["id"]
                complete = (
                    not media.truncated and not item.get("image_failures")
                    and len(saved) >= row["reported_photos"]
                )
                current_keys = {image["storage_object_key"] for image in saved.values()}
                store.save_document(media_key, {
                    "example_id": row["id"], "listing_key": row["listing_key"],
                    "listing_id": row["listing_id"], "status": "complete" if complete else "sampled",
                    "provider_media_count": len(saved),
                    "reported_photo_count": row["reported_photos"],
                    "truncated": media.truncated, "max_images": args.max_images,
                    "provider_records_examined": len(media.records),
                    "image_failures": item.get("image_failures",0),
                    "images": sorted(saved.values(), key=lambda image: (
                        image.get("context_evidence",{}).get("provider_metadata",{}).get("Order")
                            or 2147483647,
                        image.get("provider_media_key",""),
                    )),
                    "at": __import__("studio_data").now(),
                }, previous_media.get("revision", 0))
                prefix = "validation-photos/" + row["id"]
                obsolete = [
                    key for key in storage_objects(storage, project, prefix)
                    if key not in current_keys
                ]
                storage_delete(storage, project, obsolete)
                print(json.dumps(item), flush=True)
            except Exception as error:
                item["failed"] += 1
                item["error"] = type(error).__name__
                report["failed"] += 1
                print(json.dumps(item), flush=True)
            report["items"].append(item)
    finally:
        await trestle.close()
        if storage:
            storage.close()
    print(json.dumps({key:value for key,value in report.items() if key!="items"}), flush=True)
    if report["failed"]:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="List provider media without downloading or writing")
    parser.add_argument("--all", action="store_true",
                        help="Process every eligible candidate/unresolved example")
    parser.add_argument("--example-id", help="Process one validation example UUID")
    parser.add_argument("--limit", type=int, help="Process the first N eligible examples")
    parser.add_argument("--max-images", type=int, default=8, choices=range(1, 25),
                        metavar="1-24")
    args = parser.parse_args()
    if not args.dry_run and not args.all and not args.example_id:
        parser.error("Choose --dry-run, --example-id, or explicit --all")
    if args.limit is not None and not 1 <= args.limit <= 108:
        parser.error("--limit must be between 1 and 108")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
