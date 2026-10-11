"""Private Storage downloads with bounded, disposable, hash-verified disk caching."""
import hashlib
import json
import time
import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

import httpx


class StorageDownloadError(OSError):
    """Safe status/correlation, without response text, credentials or signed URLs."""
    def __init__(self, response, digest):
        self.http_status = int(response.status_code)
        candidate = response.headers.get("sb-request-id", "")
        self.request_id = candidate if re.fullmatch(r"[a-fA-F0-9-]{16,64}", candidate) else None
        self.photo_sha256 = digest
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        self.response_type = content_type if content_type in {
            "application/json", "text/plain", "text/html", "image/jpeg", "application/octet-stream"
        } else "other"
        self.service_code = None
        # An allowlist is intentional: arbitrary error messages may contain secrets.
        allowed = {"AccessDenied", "InvalidJWT", "NoSuchKey", "NoSuchBucket", "TenantNotFound",
                   "InvalidSignature", "SignatureDoesNotMatch", "InternalError", "SlowDown",
                   "DatabaseTimeout", "DatabaseError", "InvalidRequest", "unauthorized", "not_found"}
        if self.response_type == "application/json":
            body = bytearray()
            try:
                for chunk in response.iter_bytes(1024):
                    body.extend(chunk)
                    if len(body) > 4096:
                        break
                if len(body) <= 4096:
                    value = json.loads(body)
                    code = value.get("code", value.get("error")) if isinstance(value, dict) else None
                    if isinstance(code, str) and code in allowed:
                        self.service_code = code
            except (ValueError, httpx.HTTPError):
                pass
        self.retry_after = None
        raw_retry = response.headers.get("retry-after", "")
        if re.fullmatch(r"[0-9]{1,6}", raw_retry):
            self.retry_after = int(raw_retry)
        elif raw_retry:
            # Do not retry sooner than an unparsed server-specified delay.
            self.retry_after = 31
        super().__init__(f"Private photo download failed (HTTP {self.http_status}); no local fallback used")

    def safe_details(self):
        return {"http_status": self.http_status, "request_id": self.request_id,
                "photo_sha256": self.photo_sha256, "response_type": self.response_type,
                "service_code": self.service_code}


class PrivateStorage:
    def __init__(self, project, secret, cache, *, client=None, max_bytes=32*1024*1024,
                 cache_bytes=256*1024*1024):
        if not re.fullmatch(r'[a-z0-9]{20}', project):
            raise ValueError('Invalid training project reference')
        if not secret or secret.startswith('sb_publishable_'):
            raise ValueError('A server-only Storage credential is required')
        self.base = f'https://{project}.supabase.co/storage/v1/object/authenticated/'
        self.headers = {'apikey': secret, 'Authorization': 'Bearer '+secret}
        self.client = client or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.max_bytes, self.cache_bytes = max_bytes, cache_bytes
        self.lock = threading.RLock()

    def get(self, bucket, key, digest, *, force_network=False):
        if not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('Missing verified photo identity')
        if bucket != 'acq-training-private' or not key or any(
            part in {'..', '.', ''} for part in key.split('/')
        ) or '\\' in key:
            raise ValueError('Invalid private Storage reference')
        target = self.cache / digest
        with self.lock:
            if not force_network and target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                os.utime(target, None)
                return target
            # Only explicit transient HTTP failures are retried. In particular,
            # 401/403/404, redirects, missing bytes and integrity failures stop.
            for attempt in range(3):
                try:
                    return self._download_verified(bucket, key, digest, target)
                except StorageDownloadError as exc:
                    retry = (exc.http_status in {408, 429, 500, 502, 503, 504, 544}
                             and attempt < 2 and (exc.retry_after is None or exc.retry_after <= 30))
                    print(json.dumps({"event": "private_storage_read_failed", "attempt": attempt + 1,
                                      "retrying": retry, **exc.safe_details()}), flush=True)
                    if not retry:
                        raise
                    time.sleep(max(2 ** attempt, exc.retry_after or 0))
        raise RuntimeError("Unreachable storage retry state")

    def _download_verified(self, bucket, key, digest, target):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.cache, prefix='download-', delete=False) as output:
                temporary = Path(output.name)
                sha, size = hashlib.sha256(), 0
                with self.client.stream('GET', self.base+quote(bucket, safe='')+'/'+quote(key, safe='/'),
                                        headers=self.headers) as response:
                    if response.status_code != 200:
                        raise StorageDownloadError(response, digest)
                    for chunk in response.iter_bytes(64*1024):
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise OSError('Photo exceeds download limit')
                        sha.update(chunk)
                        output.write(chunk)
                if sha.hexdigest() != digest:
                    raise OSError('Photo integrity check failed')
            os.chmod(temporary, 0o600)
            temporary.replace(target)
            self._prune(target)
            return target
        except httpx.HTTPError:
            # Existing training transport retries inspect __context__. No secret
            # exception text is logged, exposed, or persisted here.
            raise OSError('Private Storage unavailable; retry later') from None
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)


    def put(self, bucket, key, data, *, content_type="application/octet-stream"):
        """Upload immutable worker artifacts to the private training bucket."""
        if bucket != 'acq-training-private' or not key or any(
            part in {'..', '.', ''} for part in key.split('/')
        ) or '\\' in key:
            raise ValueError('Invalid private Storage reference')
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError('Artifact bytes are required')
        try:
            response = self.client.post(
                self.base.replace('/object/authenticated/', '/object/') +
                quote(bucket, safe='') + '/' + quote(key, safe='/'),
                headers={**self.headers, 'Content-Type':content_type},
                content=bytes(data),
            )
        except httpx.HTTPError:
            raise OSError('Private Storage upload unavailable; retry later') from None
        if response.status_code not in {200, 201}:
            if response.status_code in {400, 409}:
                raise FileExistsError('Private artifact path already exists')
            raise OSError('Private Storage upload failed')
        return {
            'bucket':bucket, 'key':key,
            'sha256':hashlib.sha256(data).hexdigest(), 'bytes':len(data),
        }

    def _prune(self, keep):
        files = sorted((p for p in self.cache.iterdir() if p.is_file() and
                        re.fullmatch(r'[a-f0-9]{64}', p.name)), key=lambda p:p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for file in files:
            if total <= self.cache_bytes:
                break
            if file != keep:
                total -= file.stat().st_size
                file.unlink()
