"""Database-independent account controls for hosted Studio pages."""
from __future__ import annotations

from html import escape
import logging
import re
import secrets
from urllib.parse import urlparse

SESSION_SCRIPT = r'''<script>
(() => {
  'use strict';
  const originalFetch = window.fetch.bind(window);
  let session = null;
  let pendingNotice = '';
  let pendingSignedOut = false;
  function notice(message, signedOut = false) {
    pendingNotice = message;
    pendingSignedOut = signedOut;
    const element = document.getElementById('acq-session-notice');
    if (!element) return;
    element.hidden = !message;
    element.textContent = message;
    if (signedOut) {
      const link = document.createElement('a');
      link.href = '/login';
      link.textContent = ' Sign in again';
      element.appendChild(link);
    }
  }
  async function refreshSession() {
    const response = await originalFetch('/api/session', {credentials: 'same-origin', cache: 'no-store'});
    if (!response.headers.get('content-type')?.includes('application/json')) {
      throw new Error('Session endpoint did not return JSON');
    }
    const data = await response.json();
    if (response.status === 401) {
      session = null;
      notice('Your session expired. Sign in again before continuing.', true);
      return;
    }
    if (!response.ok) throw new Error('Unable to check session');
    session = data;
  }
  const ready = refreshSession().catch(() => {
    notice('Unable to check sign-in status. Retry or reload the page.');
  });
  window.fetch = async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : input, window.location.href);
    const studio = url.origin === window.location.origin && url.pathname.startsWith('/api/studio/');
    const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    let options = init;
    if (studio && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      await ready;
      if (session?.review_token) {
        const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
        headers.set('X-Review-Token', session.review_token);
        options = {...init, headers};
      }
    }
    const response = await originalFetch(input, options);
    if (studio && [401, 403, 503].includes(response.status)) {
      let data = {};
      if (response.headers.get('content-type')?.includes('application/json')) {
        try { data = await response.clone().json(); } catch (_) { /* Keep original response. */ }
      }
      if (response.status === 401) {
        session = null;
        notice('Your session expired. Sign in again before continuing.', true);
      } else if (response.status === 403 && data.code === 'review_token_expired') {
        await refreshSession().catch(() => { session = null; });
        if (session) notice('The server session was refreshed. Retry your action; no change was saved.');
        else notice('Unable to refresh the save token. Reload before retrying.');
      } else if (response.status === 503) {
        notice((data.error || 'The server is temporarily unavailable.') +
          (data.request_id ? ' Reference: ' + data.request_id : ''));
      }
    }
    // Never automatically replay a save, labeling request, or training run.
    return response;
  };
  document.addEventListener('DOMContentLoaded', () => {
    if (pendingNotice) notice(pendingNotice, pendingSignedOut);
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) window.location.reload();
  });
})();
</script>'''


def account_bar(username: str) -> bytes:
    return ('''<style>
.acq-session-bar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:10px 18px;background:#10283a;color:#eaf4ff;border-bottom:1px solid #31506a;font:14px/1.4 Segoe UI,sans-serif;box-sizing:border-box;grid-column:1/-1;flex-shrink:0}
.acq-session-bar .acq-session-account{min-width:0;overflow-wrap:anywhere}.acq-session-bar form{margin:0}.acq-session-bar button{font:inherit;color:#eaf4ff;background:#173a52;border:1px solid #7391a8;border-radius:7px;padding:8px 14px;cursor:pointer;min-height:44px}.acq-session-bar button:focus-visible{outline:3px solid #9bc5ff;outline-offset:2px}.acq-session-bar a{color:#b8d7ff}.acq-session-notice{flex-basis:100%;padding:8px 0;overflow-wrap:anywhere}
</style><div class="acq-session-bar" role="region" aria-label="Account">
<span class="acq-session-account">Signed in as <strong>''' + escape(username) + '''</strong></span>
<form method="post" action="/logout"><button type="submit">Log out</button></form>
<div id="acq-session-notice" class="acq-session-notice" role="status" aria-live="polite" hidden></div>
</div>''').encode()


def session_handler(base_handler, app, auth, cookie_name):
    """Wrap existing authentication without requiring database access for account actions."""
    class SessionHandler(base_handler):
        def reply(self, status, body, mime="text/plain; charset=utf-8", headers=None):
            if status == 200 and mime.startswith("text/html") and self.authenticated():
                body = re.sub(br'<head(?:\s[^>]*)?>', lambda m: m[0] + SESSION_SCRIPT.encode(), body, count=1, flags=re.I)
                body = re.sub(br'<body(?:\s[^>]*)?>', lambda m: m[0] + account_bar(auth.username), body, count=1, flags=re.I)
            return super().reply(status, body, mime, headers)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in {"/api/session", "/logout"}:
                if not self.trusted_host():
                    return self.data(403, {"error": "Unrecognized host"})
                if path == "/logout":
                    return self.reply(405, b"Use the Log out button.", headers=[("Allow", "POST")])
                if not self.authenticated():
                    return self.data(401, {
                        "authenticated": False, "code": "session_expired",
                        "error": "Your session expired. Sign in again.", "login": "/login",
                    })
                return self.data(200, {
                    "authenticated": True, "username": auth.username,
                    "review_token": app.token, "logout": "/logout",
                })
            return super().do_GET()

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/logout":
                if not self.trusted_host() or not self.trusted_origin():
                    return self.data(403, {"error": "Unrecognized sign-out request"})
                # Also works after expiration or when the database is unavailable.
                header = (f"{cookie_name}=; Path=/; Max-Age=0; "
                          "Expires=Thu, 01 Jan 1970 00:00:00 GMT; Secure; HttpOnly; SameSite=Strict")
                return self.reply(303, b"", headers=[("Set-Cookie", header), ("Location", "/login")])
            if path.startswith("/api/studio/"):
                if not self.trusted_host():
                    return self.data(403, {"error": "Unrecognized host"})
                if not self.authenticated():
                    return self.data(401, {
                        "code": "session_expired", "error": "Your session expired. Sign in again.",
                        "login": "/login",
                    })
                if not self.trusted_origin():
                    return self.data(403, {"code": "request_origin_rejected", "error": "Unrecognized save request"})
                if not secrets.compare_digest(self.headers.get("X-Review-Token", ""), app.token):
                    return self.data(403, {
                        "code": "review_token_expired",
                        "error": "This page has an old save token. Refresh the page and retry; no change was saved.",
                    })
            return super().do_POST()

        def storage_unavailable(self, error, *, service=False):
            sqlstate = getattr(error, "sqlstate", None)
            if sqlstate in {"42P01", "42703"}:
                category = "database_schema"
                message = "A required database table or column is unavailable. An operator must check the configured database and migrations."
            elif sqlstate == "42501":
                category = "database_permissions"
                message = "The server cannot access required database records. An operator must check its database permissions."
            elif any(cls.__name__ == "OperationalError" for cls in type(error).__mro__):
                category = "database_connection"
                message = "The server cannot connect to its database. An operator must check the database connection."
            else:
                category = "storage"
                message = "The server could not read required storage. An operator must check storage availability."
            request_id = secrets.token_hex(8)
            # Do not log exception text, query strings, credentials, or user-supplied paths.
            logging.warning(
                "storage_unavailable request_id=%s surface=%s method=%s error_type=%s sqlstate=%s",
                request_id, "actvision" if service else "studio", self.command,
                type(error).__name__, sqlstate,
            )
            return self.data(503, {
                "error": "Storage is temporarily unavailable. " + message
                         + " Operator checks include database connectivity and required Studio/ActVision migrations."
                         + " Signing in again will not repair this server error.",
                "code": "actvision_unavailable" if service else "studio_unavailable",
                "category": category, "request_id": request_id,
            })
    return SessionHandler
