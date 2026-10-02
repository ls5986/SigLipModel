"""Read-only access to the existing MLS sourcing Supabase project."""
from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import os
import socket
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from dotenv import dotenv_values
from PIL import Image, ImageOps

EXPECTED_PROJECT_REF = "uxlfqsgynbbgspaakzey"
MAX_SOURCE_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_SELECTED_MEDIA = 8

METADATA_FIELDS = {
    "address": "UnparsedAddress",
    "baths": "BathroomsTotalInteger",
    "beds": "BedroomsTotal",
    "city": "City",
    "close_date": "CloseDate",
    "close_price": "ClosePrice",
    "cumulative_days_on_market": "CumulativeDaysOnMarket",
    "days_on_market": "DaysOnMarket",
    "id": "ListingId",
    "latitude": "Latitude",
    "listed_at": "ListDate",
    "living_area": "LivingArea",
    "longitude": "Longitude",
    "lot_size": "LotSizeArea",
    "original_list_price": "OriginalListPrice",
    "postal_code": "PostalCode",
    "price": "ListPrice",
    "property_type": "PropertySubType",
    "remarks": "PublicRemarks",
    "status": "StandardStatus",
    "year_built": "YearBuilt",
}


def canonical_digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        default=str,
    ).encode()).hexdigest()


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _plain(value):
    if isinstance(value,Decimal):
        return float(value)
    if isinstance(value,(date,datetime)):
        return value.isoformat()
    if isinstance(value,dict):
        return {key:_plain(item) for key,item in value.items()}
    if isinstance(value,list):
        return [_plain(item) for item in value]
    return value


def _media_room(item):
    text = " ".join(str(item.get(key) or "") for key in (
        "category", "image_of", "long_description", "short_description",
    )).casefold()
    if "kitchen" in text:
        return "kitchen"
    if any(value in text for value in ("bathroom", "bath ", "shower", "tub", "vanity")):
        return "bathroom"
    if "bedroom" in text:
        return "bedroom"
    if any(value in text for value in ("living room", "family room", "great room")):
        return "living"
    if any(value in text for value in ("exterior", "front", "rear", "yard", "patio", "pool")):
        return "exterior"
    return "property"


def select_representative_media(selected_media, limit=MAX_SELECTED_MEDIA):
    if not isinstance(selected_media, list):
        return []
    cleaned = []
    seen = set()
    for index, source in enumerate(selected_media):
        if not isinstance(source, dict):
            continue
        url = _text(source.get("url"))
        if not url:
            continue
        key = _text(source.get("media_key")) or f"order-{source.get('order') or index + 1}"
        identity = (key, url)
        if identity in seen:
            continue
        seen.add(identity)
        cleaned.append({
            "media_key": key,
            "order": source.get("order") if isinstance(source.get("order"), int) else index + 1,
            "room": _media_room(source),
            "category": _text(source.get("category")),
            "image_of": _text(source.get("image_of")),
            "short_description": _text(source.get("short_description")),
            "long_description": _text(source.get("long_description")),
            "modification_timestamp": _text(source.get("modification_timestamp")),
            "source_url": url,
        })
    cleaned.sort(key=lambda item: (item["order"], item["media_key"]))
    chosen = []
    for room in ("kitchen", "bathroom"):
        match = next((item for item in cleaned if item["room"] == room and item not in chosen), None)
        if match:
            chosen.append(match)
    remaining = [item for item in cleaned if item not in chosen]
    slots = max(0, min(limit, len(cleaned)) - len(chosen))
    if slots and len(remaining) > slots:
        indexes = [
            round(position * (len(remaining) - 1) / max(1, slots - 1))
            for position in range(slots)
        ]
        for index in indexes:
            if remaining[index] not in chosen:
                chosen.append(remaining[index])
        for item in remaining:
            if len(chosen) >= limit:
                break
            if item not in chosen:
                chosen.append(item)
    else:
        chosen.extend(remaining[:slots])
    return chosen[:limit]


class MLSPropertySource(ABC):
    @abstractmethod
    def list_active(self, *, search="", limit=20, offset=0, exclude=()):
        raise NotImplementedError

    @abstractmethod
    def random_active(self, *, seed, limit=5, exclude=()):
        raise NotImplementedError

    @abstractmethod
    def current_opportunities(self, *, limit=5, offset=0, exclude=()):
        raise NotImplementedError

    @abstractmethod
    def listings(self, listing_keys):
        raise NotImplementedError

    @abstractmethod
    def image_bytes(self, listing_key, media_key):
        raise NotImplementedError

    def immutable_snapshot(self, listing):
        photos = []
        for media in listing.get("media", []):
            blob = self.image_bytes(listing["listing_key"], media["media_key"])
            photos.append({
                **{key: value for key, value in media.items() if key != "source_url"},
                "sha256": hashlib.sha256(blob).hexdigest(),
                "bytes": len(blob),
            })
        metadata = listing["metadata"]
        return {
            "listing_key": listing["listing_key"],
            "metadata": metadata,
            "metadata_sha256": canonical_digest(metadata),
            "photos": photos,
            "photo_manifest_sha256": canonical_digest(photos),
            "opportunity": listing.get("opportunity"),
            "source": "existing-mls-supabase-read-only",
        }


class ExistingMLSSupabaseSource(MLSPropertySource):
    def __init__(self, database_url, supabase_url, client_id=None, client_secret=None):
        project = urlparse(supabase_url).hostname or ""
        if project != f"{EXPECTED_PROJECT_REF}.supabase.co":
            raise ValueError("MLS source project does not match the approved Supabase project")
        if not database_url:
            raise ValueError("MLS source database URL is required")
        database = urlparse(database_url)
        database_identity = " ".join(filter(None,(
            database.hostname,database.username,
        )))
        if EXPECTED_PROJECT_REF not in database_identity:
            raise ValueError("MLS database URL does not identify the approved Supabase project")
        self.database_url = database_url
        self.supabase_url = supabase_url
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token = None
        self._access_token_until = 0.0

    @classmethod
    def from_env(cls):
        configured = os.environ.get("MLS_SOURCING_ENV")
        default = Path(__file__).resolve().parent.parent / "MLSSourcing" / ".env"
        values = dotenv_values(Path(configured).expanduser() if configured else default)
        return cls(
            os.environ.get("MLS_SOURCE_DATABASE_URL") or values.get("DATABASE_URL"),
            os.environ.get("MLS_SOURCE_SUPABASE_URL") or values.get("SUPABASE_URL"),
            os.environ.get("TRESTLE_CLIENT_ID") or values.get("TRESTLE_CLIENT_ID"),
            os.environ.get("TRESTLE_CLIENT_SECRET") or values.get("TRESTLE_CLIENT_SECRET"),
        )

    @contextmanager
    def connect(self):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(
            self.database_url, sslmode="require", connect_timeout=10, row_factory=dict_row,
        ) as db:
            db.execute("SET TRANSACTION READ ONLY")
            db.execute("SET LOCAL statement_timeout='30000ms'")
            yield db

    @staticmethod
    def _exclusion_sql(exclude, alias="p"):
        values = sorted({str(value) for value in exclude if value})
        return (
            f" AND NOT ({alias}.listing_key = ANY(%s))", [values],
        ) if values else ("", [])

    @staticmethod
    def _listing_select():
        return """SELECT p.listing_key,p.normalized,
            intelligence.selected_media,intelligence.facts,
            opportunity.opportunity_score,opportunity.rank AS opportunity_rank,
            opportunity.upside_dollars,opportunity.upside_percent,
            opportunity.explanation AS opportunity_explanation
          FROM public.mls_properties p
          LEFT JOIN LATERAL (
            SELECT i.selected_media,i.facts FROM public.mls_property_intelligence i
            WHERE i.listing_key=p.listing_key
            ORDER BY i.updated_at DESC,i.id DESC LIMIT 1
          ) intelligence ON true
          LEFT JOIN LATERAL (
            SELECT o.opportunity_score,o.rank,o.upside_dollars,o.upside_percent,o.explanation
            FROM public.mls_opportunities o
            JOIN public.mls_run_properties rp ON rp.id=o.run_property_id
            WHERE o.is_current=true AND rp.listing_key=p.listing_key
            ORDER BY o.opportunity_score DESC NULLS LAST,o.created_at DESC LIMIT 1
          ) opportunity ON true"""

    @staticmethod
    def _clean_row(row):
        normalized = row.get("normalized") if isinstance(row.get("normalized"), dict) else {}
        metadata = {
            target: _plain(normalized.get(source))
            for source, target in METADATA_FIELDS.items()
            if normalized.get(source) is not None
        }
        metadata["ListingKey"] = str(row["listing_key"])
        opportunity = None
        if row.get("opportunity_score") is not None:
            opportunity = {
                "score": _plain(row.get("opportunity_score")),
                "rank": _plain(row.get("opportunity_rank")),
                "upside_dollars": _plain(row.get("upside_dollars")),
                "upside_percent": _plain(row.get("upside_percent")),
                "explanation": row.get("opportunity_explanation"),
                "notice": "Existing MLS opportunity score is context, not training ground truth.",
            }
        return {
            "id": f"mls:{row['listing_key']}",
            "listing_key": str(row["listing_key"]),
            "address": metadata.get("UnparsedAddress") or str(row["listing_key"]),
            "city": metadata.get("City"),
            "postal_code": metadata.get("PostalCode"),
            "metadata": metadata,
            "metadata_sha256": canonical_digest(metadata),
            "media": select_representative_media(row.get("selected_media")),
            "opportunity": opportunity,
            "source": "existing-mls-supabase-read-only",
        }

    def list_active(self, *, search="", limit=20, offset=0, exclude=()):
        limit, offset = min(40, max(1, int(limit))), max(0, int(offset))
        search = str(search or "").strip()
        exclusion, excluded_params = self._exclusion_sql(exclude)
        where = "WHERE lower(coalesce(p.normalized->>'status',''))='active'"
        params = []
        if search:
            where += """ AND concat_ws(' ',p.listing_key,p.normalized->>'id',
                p.normalized->>'address',p.normalized->>'city',
                p.normalized->>'postal_code') ILIKE %s"""
            params.append(f"%{search}%")
        params.extend(excluded_params)
        query = f"""{self._listing_select()} {where} {exclusion}
            ORDER BY p.modification_timestamp DESC NULLS LAST,p.listing_key
            LIMIT %s OFFSET %s"""
        params.extend((limit, offset))
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [self._clean_row(row) for row in rows]

    def random_active(self, *, seed, limit=5, exclude=()):
        limit = min(20, max(1, int(limit)))
        seed = str(seed or "").strip()
        if not seed:
            raise ValueError("A deterministic random seed is required")
        exclusion, params = self._exclusion_sql(exclude)
        query = f"""{self._listing_select()}
            WHERE lower(coalesce(p.normalized->>'status',''))='active' {exclusion}
            ORDER BY md5(p.listing_key || %s),p.listing_key LIMIT %s"""
        params.extend((seed, limit))
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [self._clean_row(row) for row in rows]

    def current_opportunities(self, *, limit=5, offset=0, exclude=()):
        limit, offset = min(20, max(1, int(limit))), max(0, int(offset))
        exclusion, params = self._exclusion_sql(exclude)
        query = f"""{self._listing_select()}
            WHERE EXISTS (
              SELECT 1 FROM public.mls_opportunities current_o
              JOIN public.mls_run_properties current_rp
                ON current_rp.id=current_o.run_property_id
              WHERE current_o.is_current=true
                AND current_rp.listing_key=p.listing_key
            ) {exclusion}
            ORDER BY opportunity.opportunity_score DESC NULLS LAST,
              opportunity.rank,p.listing_key LIMIT %s OFFSET %s"""
        params.extend((limit, offset))
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [self._clean_row(row) for row in rows]

    def listings(self, listing_keys):
        keys = list(dict.fromkeys(str(key) for key in listing_keys if key))
        if not keys:
            return []
        if len(keys) > 40:
            raise ValueError("At most 40 MLS listings may be loaded together")
        query = f"""{self._listing_select()}
            WHERE p.listing_key = ANY(%s) ORDER BY p.listing_key"""
        with self.connect() as db:
            rows = db.execute(query, (keys,)).fetchall()
        by_key = {str(row["listing_key"]): self._clean_row(row) for row in rows}
        return [by_key[key] for key in keys if key in by_key]

    def _token(self):
        if not self.client_id or not self.client_secret:
            raise ValueError("Trestle credentials are required to retrieve MLS photo bytes")
        if self._access_token and time.monotonic()<self._access_token_until:
            return self._access_token
        with httpx.Client(
            base_url="https://api.cotality.com", timeout=30, trust_env=False,
        ) as client:
            response = client.post(
                "/trestle/oidc/connect/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                    "scope": "api",
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
            token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Trestle authentication response omitted access_token")
        self._access_token = token
        self._access_token_until = time.monotonic()+max(
            60,int(payload.get("expires_in",28800))-300,
        )
        return self._access_token

    @staticmethod
    def _validate_redirect(url):
        parsed = urlparse(url)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.port not in {None, 443}
        ):
            raise ValueError("MLS photo redirect is not a safe HTTPS URL")
        addresses = socket.getaddrinfo(parsed.hostname, 443, 0, socket.SOCK_STREAM)
        if not addresses or any(
            not ipaddress.ip_address(item[4][0]).is_global for item in addresses
        ):
            raise ValueError("MLS photo redirect must resolve only to public addresses")

    @staticmethod
    def _validate_source_url(url):
        parsed = urlparse(url)
        if (
            parsed.scheme!="https" or parsed.hostname!="api.cotality.com"
            or parsed.username or parsed.password or parsed.port not in {None,443}
            or not parsed.path.startswith("/trestle/Media/")
        ):
            raise ValueError("MLS photo source is not an approved Cotality media URL")

    @staticmethod
    def _bounded_blob(response):
        content_type = response.headers.get("content-type", "").lower()
        if not content_type.startswith("image/"):
            raise ValueError("MLS photo response was not an image")
        declared = response.headers.get("content-length")
        if declared and int(declared) > MAX_SOURCE_BYTES:
            raise ValueError("MLS photo exceeds the bounded source size")
        blob = response.content
        if not 0 < len(blob) <= MAX_SOURCE_BYTES:
            raise ValueError("MLS photo exceeds the bounded source size")
        with Image.open(io.BytesIO(blob)) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("MLS photo exceeds the pixel limit")
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((3000, 3000))
            output = io.BytesIO()
            image.save(output, "JPEG", quality=88, optimize=True)
        return output.getvalue()

    @lru_cache(maxsize=64)
    def _download(self, url):
        self._validate_source_url(url)
        token = self._token()
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            response = client.get(
                url, headers={"Authorization": f"Bearer {token}", "Accept": "image/*"},
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                destination = urljoin(url, response.headers.get("location", ""))
                self._validate_redirect(destination)
                response = client.get(destination, headers={"Accept": "image/*"})
            response.raise_for_status()
            return self._bounded_blob(response)

    def image_bytes(self, listing_key, media_key):
        listings = self.listings([listing_key])
        if not listings:
            raise ValueError("MLS listing is unavailable")
        media = next((
            item for item in listings[0]["media"]
            if item["media_key"] == str(media_key)
        ), None)
        if not media:
            raise ValueError("MLS photo is outside the selected evidence set")
        return self._download(media["source_url"])
