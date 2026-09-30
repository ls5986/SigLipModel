"""Private Storage downloads with bounded, disposable, hash-verified disk caching."""
import hashlib
import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

import httpx


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

    def get(self, bucket, key, digest):
        if not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('Missing verified photo identity')
        if bucket != 'acq-training-private' or not key or any(
            part in {'..', '.', ''} for part in key.split('/')
        ) or '\\' in key:
            raise ValueError('Invalid private Storage reference')
        target = self.cache / digest
        with self.lock:
            if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                os.utime(target, None)
                return target
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.cache, prefix='download-', delete=False) as output:
                    temporary = Path(output.name)
                    sha, size = hashlib.sha256(), 0
                    with self.client.stream('GET', self.base+quote(bucket, safe='')+'/'+quote(key, safe='/'),
                                            headers=self.headers) as response:
                        if response.status_code != 200:
                            raise OSError('Private photo download failed; no local fallback used')
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
                raise OSError('Private Storage unavailable; retry later') from None
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)

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
