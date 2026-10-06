"""Authenticated hosted review server for the Supabase Studio backend."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
import mimetypes
import os
import secrets
import time
from functools import lru_cache
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from psycopg import OperationalError
from psycopg.errors import InsufficientPrivilege, UndefinedColumn, UndefinedTable

from cloud_runtime import from_env
from config import CODE_ROOT
from PIL import Image, ImageOps

COOKIE = "acq_studio_session"
STORAGE_UNAVAILABLE_ERRORS = (OSError, OperationalError, UndefinedTable, UndefinedColumn, InsufficientPrivilege)
LOGIN = b'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ACQ Vision sign in</title><style>body{margin:0;background:#071521;color:#eaf4ff;font:16px/1.5 Segoe UI,sans-serif;min-height:100vh;display:grid;place-items:center}.card{width:min(380px,calc(100% - 40px));background:#10283a;border:1px solid #31506a;border-radius:18px;padding:28px;box-shadow:0 24px 70px #0008}h1{margin:0 0 8px}p{color:#9fb4c7;margin:0 0 22px}label{display:grid;gap:6px;margin:14px 0}input,button{font:inherit;padding:12px;border-radius:9px;border:1px solid #49667d}input{background:#071521;color:#fff}button{width:100%;margin-top:12px;background:#4c80ff;color:#fff;font-weight:700}.error{color:#ff9c9c}</style></head><body><form class="card" method="post" action="/login"><h1>ACQ Vision Studio</h1><p>Private development workspace</p>__ERROR__<label>Email<input name="username" type="email" autocomplete="username" required autofocus></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label><button>Sign in</button></form></body></html>'''


class HostedAuth:
    def __init__(self):
        self.origin = os.environ.get("STUDIO_PUBLIC_ORIGIN", "").strip().rstrip("/")
        parsed = urlparse(self.origin)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path:
            raise ValueError("STUDIO_PUBLIC_ORIGIN must be an HTTPS origin without a path")
        self.host = parsed.netloc
        self.username = os.environ.get("STUDIO_LOGIN_USERNAME", "").strip().casefold()
        self.password = os.environ.get("STUDIO_LOGIN_PASSWORD", "")
        self.secret = os.environ.get("STUDIO_SESSION_SECRET", "").encode()
        if not self.username or len(self.password) < 14 or len(self.secret) < 32:
            raise ValueError("Hosted login needs an email, a 14+ character password and a 32+ character session secret")

    def issue(self):
        expires = str(int(time.time()) + 12 * 3600)
        signature = hmac.new(self.secret, (self.username+":"+expires).encode(), hashlib.sha256).digest()
        return expires+"."+base64.urlsafe_b64encode(signature).decode().rstrip("=")

    def valid(self, header):
        try:
            jar = cookies.SimpleCookie();jar.load(header or "")
            value = jar[COOKIE].value
            expires, supplied = value.split(".", 1)
            expected = base64.urlsafe_b64encode(hmac.new(
                self.secret, (self.username+":"+expires).encode(), hashlib.sha256
            ).digest()).decode().rstrip("=")
            return int(expires) > time.time() and hmac.compare_digest(supplied, expected)
        except (KeyError, ValueError, TypeError):
            return False


def create_server(port, app, auth):
    @lru_cache(maxsize=256)
    def thumbnail(path, modified, size):
        with Image.open(path) as original:
            if original.width * original.height > 40_000_000:
                raise ValueError("Image dimensions exceed thumbnail limit")
            image = ImageOps.exif_transpose(original)
            image.thumbnail((320, 220))
            output = io.BytesIO()
            image.convert("RGB").save(output, "JPEG", quality=76, optimize=True)
            return output.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def body(self, maximum):
            try: length = int(self.headers.get("Content-Length", "0"))
            except ValueError: raise ValueError("Invalid request size") from None
            if not 0 < length <= maximum: raise ValueError("Invalid request size")
            return self.rfile.read(length)

        def reply(self, status, body, mime="text/plain; charset=utf-8", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for key,value in headers or []: self.send_header(key,value)
            self.end_headers();self.wfile.write(body)

        def data(self, status, payload):
            self.reply(status,json.dumps(payload).encode(),"application/json; charset=utf-8")

        def trusted_host(self):
            return self.headers.get("Host", "").casefold() == auth.host.casefold()

        def trusted_origin(self):
            supplied=self.headers.get("Origin", "")
            if supplied and supplied!="null":
                return supplied.strip().rstrip("/").casefold()==auth.origin.casefold()
            referer=self.headers.get("Referer", "")
            if referer:
                parsed=urlparse(referer)
                return f"{parsed.scheme}://{parsed.netloc}".casefold()==auth.origin.casefold()
            return self.headers.get("Sec-Fetch-Site", "").casefold()=="same-origin"

        def log_rejected_request(self,event):
            print(json.dumps({
                "event":event,
                "path":urlparse(self.path).path,
                "trusted_host":self.trusted_host(),
                "origin_present":bool(self.headers.get("Origin")),
                "trusted_origin":self.trusted_origin(),
                "same_origin_fetch":self.headers.get("Sec-Fetch-Site", "").casefold()=="same-origin",
            }),flush=True)

        def authenticated(self):
            return self.trusted_host() and auth.valid(self.headers.get("Cookie"))

        def storage_unavailable(self, error, *, service=False):
            logging.warning("storage_unavailable surface=%s method=%s error_type=%s",
                            "actvision" if service else "studio", self.command, type(error).__name__)
            return self.data(503, {
                "error": "Storage is temporarily unavailable. Ask an operator to verify database connectivity "
                         "and the required Studio/ActVision migrations before retrying.",
                "code": "actvision_unavailable" if service else "studio_unavailable",
            })

        def service_request(self, path):
            from actvision_service import UnavailableError, approved_manifest, infer, receive_feedback
            token = os.environ.get("ACTVISION_SERVICE_TOKEN", "")
            if not self.trusted_host():
                return self.data(403, {"error": "Unrecognized host"})
            if len(token) < 32:
                return self.data(503, {"error": "ActVision service bridge is disabled", "code": "actvision_unavailable"})
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                return self.data(401, {"error": "ActVision service credentials required"})
            try:
                studio = app.get_studio()
                store = getattr(studio, "store", None)
                if self.command == "GET" and path.startswith("/api/actvision/v2/releases/"):
                    release_id = unquote(path.removeprefix("/api/actvision/v2/releases/"), errors="strict")
                    try:
                        manifest = approved_manifest(store, release_id)
                    except UnavailableError:
                        return self.data(404, {"error": "Release not found"})
                    return self.data(200, manifest)
                if self.command != "POST" or path not in {"/api/actvision/v2/infer", "/api/actvision/v2/feedback"}:
                    return self.data(404, {"error": "Not found"})
                payload = json.loads(self.body(2_000_000))
                if path.endswith("/infer"):
                    from actvision_contract import validate_contract
                    validate_contract(payload, "inference_request")
                    workspace = os.environ.get("ACTVISION_SOURCE_WORKSPACE_ID") or os.environ.get("STUDIO_WORKSPACE_ID")
                    if not workspace or payload["evidence"]["workspace_id"] != workspace:
                        raise PermissionError("Inference source workspace is not authorized")
                    return self.data(200, infer(store, payload))
                if store is None:
                    raise UnavailableError("ActVision cloud training store is unavailable")
                return self.data(200, receive_feedback(store, payload))
            except UnavailableError as exc:
                return self.data(503, {"error": str(exc), "code": "actvision_unavailable"})
            except PermissionError as exc:
                return self.data(403, {"error": str(exc)})
            except RuntimeError as exc:
                return self.data(409, {"error": str(exc)})
            except (ValueError, TypeError, KeyError) as exc:
                return self.data(400, {"error": str(exc)})
            except STORAGE_UNAVAILABLE_ERRORS as exc:
                return self.storage_unavailable(exc, service=True)

        def do_GET(self):
            path=urlparse(self.path).path
            if path.startswith("/api/actvision/v2/"):
                return self.service_request(path)
            if path=="/health":
                return self.data(200,{
                    "status":"ready","storage":"supabase",
                    "version":os.environ.get("RENDER_GIT_COMMIT","unknown")[:12],
                })
            if not self.trusted_host():
                return self.data(403,{"error":"Unrecognized host"})
            if path=="/login":
                if self.authenticated():
                    return self.reply(303,b"",headers=[("Location","/")])
                return self.reply(200,LOGIN.replace(b"__ERROR__",b""),"text/html; charset=utf-8")
            if not self.authenticated():
                if path.startswith("/api/"):
                    return self.data(401,{"error":"Your session expired. Sign in again.","login":"/login"})
                return self.reply(303,b"",headers=[("Location","/login")])
            if path=="/" and parse_qs(urlparse(self.path).query).get("property"):
                return self.reply(303,b"",headers=[("Location","/studio?"+urlparse(self.path).query)])
            if path=="/":
                return self.reply(200,(CODE_ROOT/"studio_home.html").read_bytes(),"text/html; charset=utf-8")
            if path=="/studio":
                return self.reply(200,(CODE_ROOT/"training_studio.html").read_bytes(),"text/html; charset=utf-8")
            if path in {"/advanced", "/workbench", "/legacy", "/research"}:
                return self.reply(303,b"",headers=[("Location","/studio")])
            if path == "/mls-validation":
                page = "mls_validation_ui.html"
                return self.reply(200,(CODE_ROOT/page).read_bytes(),"text/html; charset=utf-8")
            if path=="/research":
                return self.reply(503,b"Prompt-lab research tools require the local research backend. No paid/model action was started.")
            if path in {"/property-review", "/review"}:
                return self.reply(303,b"",headers=[("Location","/studio"+("?"+urlparse(self.path).query if urlparse(self.path).query else ""))])
            if path=="/source-evidence":
                page=(CODE_ROOT/"review_ui.html").read_bytes().replace(
                    b"Local preview",b"Cloud development"
                ).replace(
                    b'/advanced?tab=experiments',b'/status#models'
                ).replace(
                    b'Models &amp; results \xe2\x86\x97',b'Training status'
                ).replace(
                    b'/advanced?tab=dataset',b'/status#data'
                ).replace(
                    b'Data \xe2\x86\x97',b'Data coverage'
                )
                return self.reply(200,page,"text/html; charset=utf-8")
            if path=="/source-rows":
                return self.reply(200,(CODE_ROOT/"source_rows.html").read_bytes(),"text/html; charset=utf-8")
            if path=="/status":
                return self.reply(
                    200,(CODE_ROOT/"hosted_status.html").read_bytes(),"text/html; charset=utf-8"
                )
            if path=="/workbench":
                return self.reply(
                    200,(CODE_ROOT/"workbench_ui.html").read_bytes(),"text/html; charset=utf-8"
                )
            if path.startswith("/api/studio/"):
                try:
                    if path=="/api/studio/workbench/challenge-image":
                        args=parse_qs(urlparse(self.path).query)
                        blob=app.get_studio().challenge_image(
                            args.get("listing_key",[""])[0],
                            args.get("media_key",[""])[0],
                            args.get("batch_id",[None])[0],
                        )
                        return self.reply(200,blob,"image/jpeg")
                    if path in {"/api/studio/image","/api/studio/thumbnail"}:
                        identifier=parse_qs(urlparse(self.path).query).get("id",[""])[0]
                        file=app.get_studio().store.image_path(identifier)
                        if path=="/api/studio/thumbnail":
                            info=file.stat()
                            return self.reply(
                                200,thumbnail(file,info.st_mtime_ns,info.st_size),"image/jpeg"
                            )
                        return self.reply(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or "image/jpeg")
                    return self.data(200,app.get_studio().get(self.path))
                except (ValueError,TypeError) as exc:
                    return self.data(400,{"error":str(exc)})
                except STORAGE_UNAVAILABLE_ERRORS as exc:
                    return self.storage_unavailable(exc)
            return self.data(404,{"error":"Not found"})

        def do_POST(self):
            path=urlparse(self.path).path
            if path.startswith("/api/actvision/v2/"):
                return self.service_request(path)
            if path=="/login":
                if not self.trusted_host() or not self.trusted_origin():
                    self.log_rejected_request("login_origin_rejected")
                    return self.data(403,{"error":"Unrecognized sign-in request"})
                try:
                    fields={k:v[0] for k,v in parse_qs(self.body(8192).decode()).items()}
                except (ValueError,UnicodeDecodeError):
                    return self.data(400,{"error":"Invalid sign-in request"})
                valid_user=hmac.compare_digest(fields.get("username","").strip().casefold(),auth.username)
                valid_password=hmac.compare_digest(fields.get("password",""),auth.password)
                if not valid_user or not valid_password:
                    time.sleep(.25)
                    return self.reply(401,LOGIN.replace(b"__ERROR__",b'<p class="error">Email or password is incorrect</p>'),"text/html; charset=utf-8")
                header=f"{COOKIE}={auth.issue()}; Path=/; Max-Age=43200; Secure; HttpOnly; SameSite=Strict"
                return self.reply(303,b"",headers=[("Set-Cookie",header),("Location","/")])
            if not self.authenticated() or not self.trusted_origin() or not secrets.compare_digest(
                self.headers.get("X-Review-Token",""),app.token
            ):
                self.log_rejected_request("review_request_rejected")
                return self.data(403,{"error":"Sign in again before saving"})
            if not path.startswith("/api/studio/"):
                return self.data(404,{"error":"Not found"})
            try:
                payload=json.loads(self.body(2_000_000))
                if not isinstance(payload,dict): raise ValueError("Expected a JSON object")
                from studio_v2 import TRAINING_ACTIONS, require_operator
                if path in TRAINING_ACTIONS:
                    require_operator()
                return self.data(200,app.get_studio().post(path,payload))
            except PermissionError as exc:
                return self.data(403,{"error":str(exc)})
            except RuntimeError as exc:
                return self.data(409,{"error":str(exc)})
            except (ValueError,TypeError,KeyError) as exc:
                return self.data(400,{"error":str(exc)})
            except STORAGE_UNAVAILABLE_ERRORS as exc:
                return self.storage_unavailable(exc)

    from hosted_session import session_handler
    return ThreadingHTTPServer(("0.0.0.0",port),session_handler(Handler, app, auth, COOKIE))


def main():
    app=from_env();auth=HostedAuth();port=int(os.environ.get("PORT","10000"))
    server=create_server(port,app,auth)
    from openai_labels import start_hosted_worker
    stop = start_hosted_worker(app.get_studio().store)
    print(json.dumps({"status":"ready","port":port,"storage":"supabase"}),flush=True)
    try: server.serve_forever()
    finally:
        if stop: stop.set()
        server.server_close()

if __name__=="__main__": main()
