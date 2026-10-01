"""Outbound-only, paired ACQ BOT dev worker. No database or account credentials."""
import json
import os
import threading
import time

import httpx

from model_loop import status
from pilot import read_json

DEV_ORIGIN = "https://mls-acquisition-dev-web.onrender.com"


def remote(origin, token, path, payload=None):
    if origin != DEV_ORIGIN:
        raise ValueError("Only the ACQ BOT dev origin is allowed for this experiment")
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
        response = client.post(origin + "/api/local-siglip/" + path,
                               headers={"Authorization": "Bearer " + token}, json=payload or {})
        if response.status_code in {401, 403}:
            raise PermissionError("Connection expired or revoked. Reconnect from ACQ BOT dev.")
        if response.status_code == 409:
            raise ValueError("The remote comparison changed or expired. Run a fresh test in ACQ BOT.")
        response.raise_for_status()
        return response.json()


class ConnectedWorker:
    def __init__(self, exchange, call=remote, autostart=True):
        self.exchange = exchange
        self.jobs = exchange.jobs
        self.call = call
        self.file = self.jobs.root / "data" / "acq_connection.json"
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.state = read_json(self.file) if self.file.exists() else None
        self.message = "Not connected"
        self.checked = 0
        self.capability = None
        if self.state and autostart:
            self.start()

    def save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        if self.state is None:
            self.file.unlink(missing_ok=True)
            return
        temp = self.file.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle)
        temp.replace(self.file)

    def public_status(self):
        return {"connected": self.state is not None, "origin": self.state.get("origin") if self.state else None,
                "message": self.message, "active": bool(self.state and self.state.get("active"))}

    def connect(self, payload):
        if payload.get("origin") != DEV_ORIGIN or payload.get("confirmed") is not True:
            raise ValueError("Confirm a connection to ACQ BOT dev")
        code = payload.get("code")
        if not isinstance(code, str) or not 20 <= len(code) <= 100:
            raise ValueError("Connection code is invalid. Connect again from ACQ BOT dev.")
        if self.state:
            self.disconnect()
        try:
            result = self.call(DEV_ORIGIN, code, "claim")
        except PermissionError as exc:
            raise ValueError("The connection code expired or was replaced. Connect again from ACQ BOT dev.") from exc
        except httpx.HTTPError as exc:
            raise ValueError("Cannot reach ACQ BOT dev. Check its deployment, then retry connecting.") from exc
        with self.lock:
            self.state = {"origin": DEV_ORIGIN, "token": result["token"], "active": None}
            self.save()
            self.message = "Connected. Checking the reviewed model…"
            self.start()
        return self.public_status()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def disconnect(self):
        self.stop.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(25)
            if self.thread.is_alive():
                raise ValueError("Connection request is still finishing. Retry disconnect shortly.")
        if self.state:
            active = self.state.get("active")
            if active and active.get("local_id"):
                self.exchange.cancel(active["local_id"])
            try:
                self.call(self.state["origin"], self.state["token"], "disconnect")
            except (httpx.HTTPError, PermissionError, ValueError):
                # Local credential removal still succeeds; remote revocation is
                # also available in ACQ BOT and the credential has an expiry.
                pass
        self.state = None
        self.save()
        self.message = "Disconnected. You can also revoke the connection in ACQ BOT."
        return self.public_status()

    def get_capability(self):
        if self.capability is None or time.monotonic() - self.checked > 60:
            state = status(self.jobs)
            self.capability = {key: state.get(key) for key in ("version", "heads_sha256", "review_fingerprint")}
            self.capability.update(
                ready=state["ready"], reason=state["reason"][:400],
                components=state.get("components", {}),
                component_versions=state.get("component_versions", {}),
                prediction_modes=[
                    "automatic", "images_only", "metadata_only", "images_and_metadata",
                ],
                request_schema_versions=["acq-siglip-request-v1", "acq-property-request-v2"],
            )
            self.checked = time.monotonic()
        return {**self.capability, "busy": bool(self.jobs.active and self.jobs.active.is_alive())}

    def tick(self):
        state = self.state
        if not state:
            return
        origin, token = state["origin"], state["token"]
        active = state.get("active")
        if active:
            result = self.exchange.result(active["local_id"]) if active.get("local_id") else {"status": "failed"}
            if result["status"] != "running":
                payload = {"task_id": active["id"], "lease": active["lease"]}
                if result["status"] == "completed":
                    payload["result"] = result["result"]
                else:
                    payload["error"] = "Local Studio could not finish. Check its model/reviews, then run a fresh test."
                try:
                    self.call(origin, token, "complete", payload)
                    self.message = "Result returned to ACQ BOT." if result["status"] == "completed" else payload["error"]
                except ValueError:
                    self.message = "Comparison cancelled or expired in ACQ BOT. No automatic retry."
                state["active"] = None
                self.save()
                return
        reply = self.call(origin, token, "poll", {"capability": self.get_capability(),
                          "active_task": active["id"] if active else None, "lease": active["lease"] if active else None})
        if reply.get("cancel_active") and active:
            if active.get("local_id"):
                self.exchange.cancel(active["local_id"])
            state["active"] = None
            self.save()
            self.message = "Comparison cancelled by ACQ BOT."
            return
        task = reply.get("task")
        if task:
            # Save the lease before starting. Restart reports interruption rather
            # than silently re-running a potentially completed model task.
            state["active"] = {"id": task["id"], "lease": task["lease"], "local_id": None}
            self.save()
            try:
                current = status(self.jobs)
                if not current["ready"] or any(current.get(k) != task["model"].get(k) for k in ("version", "heads_sha256", "review_fingerprint")):
                    raise ValueError("Model/reviews changed")
                preview = self.exchange.preview(task["request"])
                state["active"]["local_id"] = preview["id"]
                self.save()
                self.exchange.start({"id": preview["id"], "confirmed": True})
                self.message = "Processing the selected ACQ BOT property locally."
            except (ValueError, OSError, KeyError):
                state["active"]["local_id"] = None
                self.save()
                self.message = "Model or reviews changed. Reporting failure; no automatic retraining."
        elif not active:
            self.message = "Connected and waiting for a property test." if self.capability["ready"] else self.capability["reason"]

    def loop(self):
        while not self.stop.is_set() and self.state:
            try:
                self.tick()
            except PermissionError as exc:
                active = self.state.get("active") if self.state else None
                if active and active.get("local_id"):
                    self.exchange.cancel(active["local_id"])
                self.state = None
                self.save()
                self.message = str(exc)
                return
            except (httpx.HTTPError, ValueError, OSError, KeyError):
                self.message = "Connection interrupted. Keeping any saved result and retrying the connection."
            self.stop.wait(10 if self.state and self.state.get("active") else 30)
