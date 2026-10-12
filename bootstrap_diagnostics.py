"""Persist safe startup failures without logging credentials, SQL, or row contents."""
from datetime import datetime, timezone
from functools import wraps
import json
from pathlib import Path
import re

MARKER = 'actvision-v2-auto-bootstrap-once'


def safe_failure(error):
    chain, seen = [], set()
    while error is not None and id(error) not in seen and len(chain) < 4:
        seen.add(id(error))
        frames, tb = [], error.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            frames.append({'file': Path(code.co_filename).name,
                           'function': code.co_name, 'line': tb.tb_lineno})
            tb = tb.tb_next
        sqlstate = getattr(error, 'sqlstate', None)
        chain.append({'error_type': type(error).__name__,
                      'sqlstate': sqlstate if isinstance(sqlstate, str) and re.fullmatch(r'[A-Z0-9]{5}', sqlstate) else None,
                      'frames': frames[-8:]})
        error = error.__cause__ or error.__context__
    return {'errors': chain}


def capture_bootstrap_failure(function):
    @wraps(function)
    def wrapped(store, *args, **kwargs):
        try:
            return function(store, *args, **kwargs)
        except Exception as error:
            diagnostic = safe_failure(error)
            print(json.dumps({'event': 'actvision_bootstrap_failed', **diagnostic}), flush=True)
            # A startup failure never grants permission to replay a training job.
            marker = store.document(MARKER) or {}
            if marker.get('status') in {'queued', 'completed', 'failed'}:
                return marker
            payload = {'status': 'failed', 'at': datetime.now(timezone.utc).isoformat(),
                       'reason': 'Training startup failed before queue confirmation; inspect safe diagnostics.',
                       'diagnostic': diagnostic}
            return store.save_document(MARKER, payload, marker.get('revision', 0))
    return wrapped
