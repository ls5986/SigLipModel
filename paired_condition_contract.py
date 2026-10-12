"""Lightweight paired research contract shared by the web app and CPU worker."""
import hashlib
import json

POLICY = 'paired-condition-heads-v1'
SCHEMA = 'actvision-paired-condition-v1'
KNOWN = {'TARGET', 'NOT_TARGET'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True).encode()).hexdigest()
