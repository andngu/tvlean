"""`tvlean serve`: a small local web UI over the same engine the CLI uses.

Stdlib only. Every /api call needs the session token printed at startup, so
another website open in the same browser can't drive the TV.
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import urlparse

from . import __version__, engine
from .device import AdbError, Device, connect, list_devices
from .profiles import Profile, ProfileError, find_profile

# adb commands against one device must not interleave (apply + status racing would plan stale state).
_device_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _profile_for(device: Device) -> Profile:
    model = device.model()
    prof = find_profile(model)
    if not prof:
        raise ApiError(404, f"No profile for model {model!r} yet. See CONTRIBUTING.md to add one.")
    return prof


def _ready_device(serial: str) -> Device:
    if (serial, "device") not in list_devices():
        raise ApiError(409, f"{serial} is not connected or not authorized")
    return Device(serial)


def api_devices(_body: dict) -> dict:
    return {"devices": [{"serial": s, "state": st} for s, st in list_devices()]}


def api_connect(body: dict) -> dict:
    addr = str(body.get("address", "")).strip()
    if not addr:
        raise ApiError(400, "address required")
    if ":" not in addr:
        addr += ":5555"
    return {"message": connect(addr), **api_devices({})}


def _group_items(prof: Profile, gid: str, pending: list[engine.Change], installed: set[str]) -> list[dict]:
    """Every rule in the group with its status on this device, so done groups still explain themselves."""
    by_target = {c.target: c for c in pending}
    items = []
    for rule in prof.rules_in(gid):
        change = by_target.get(rule.target)
        if change:
            status = "pending"
        elif rule.kind != "setting" and rule.package not in installed:
            status = "absent"
        else:
            status = "done"
        items.append(
            {
                "kind": rule.kind,
                "target": rule.target,
                "label": rule.label,
                "reason": rule.reason,
                "status": status,
                "before": change.before if change else None,
                "after": change.after if change else None,
            }
        )
    # Changes the engine adds on its own (e.g. the accessibility master switch).
    covered = {r.target for r in prof.rules_in(gid)}
    for c in pending:
        if c.target not in covered:
            items.append(
                {
                    "kind": c.kind,
                    "target": c.target,
                    "label": c.label,
                    "reason": c.reason,
                    "status": "pending",
                    "before": c.before,
                    "after": c.after,
                }
            )
    return items


def api_status(body: dict) -> dict:
    dev = _ready_device(body["device"])
    prof = _profile_for(dev)
    installed = dev.installed_packages()
    mem = dev.mem_available_kb()
    groups = []
    for gid, group in prof.groups.items():
        changes = engine.plan(dev, prof, [gid])
        groups.append(
            {
                "id": gid,
                "title": group.title,
                "description": group.description,
                "caveat": group.caveat,
                "default": gid in prof.default_groups,
                "pending": [asdict(c) for c in changes],
                "items": _group_items(prof, gid, changes, installed),
            }
        )
    warnings = [
        f"Protected package {p.package} is disabled. {p.reason}"
        for p in prof.protected
        if p.package in installed and dev.package_state(p.package) in engine.DISABLED_STATES
    ]
    journal = engine.latest_journal(dev.serial)
    return {
        "device": {
            "serial": dev.serial,
            "model": dev.model(),
            "android": dev.prop("ro.build.version.release"),
            "free_ram_mb": mem // 1024 if mem else None,
            "home": dev.home_activity(),
            "home_ok": engine.home_is_healthy(dev),
            "foreground": dev.foreground_package(),
        },
        "profile": {"id": prof.id, "name": prof.name, "tested_on": prof.tested_on},
        "groups": groups,
        "warnings": warnings,
        "can_restore": journal is not None,
    }


def api_apply(body: dict) -> dict:
    dev = _ready_device(body["device"])
    prof = _profile_for(dev)
    groups = [g for g in body.get("groups", []) if g in prof.groups]
    if not groups:
        raise ApiError(400, "pick at least one group")
    changes = engine.plan(dev, prof, groups)
    if not changes:
        return {"applied": 0, "notes": []}
    journal = engine.apply(dev, prof, changes)
    notes = [
        f"Pressed Home but {pkg} didn't come up. {hint}"
        for pkg, hint in engine.verify_home_overrides(dev, prof, changes)
    ]
    return {"applied": len(changes), "journal": str(journal), "notes": notes}


def api_restore(body: dict) -> dict:
    dev = _ready_device(body["device"])
    path = engine.latest_journal(dev.serial)
    if not path:
        raise ApiError(404, "nothing to restore")
    return {"restored": len(engine.restore(dev, path))}


ROUTES = {
    ("GET", "/api/devices"): api_devices,
    ("POST", "/api/connect"): api_connect,
    ("POST", "/api/status"): api_status,
    ("POST", "/api/apply"): api_apply,
    ("POST", "/api/restore"): api_restore,
}


def make_handler(token: str):
    page = (resources.files("tvlean") / "static" / "index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"tvlean/{__version__}"

        def log_message(self, fmt, *args):  # keep the terminal quiet; errors still surface in the UI
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")  # the token lives in the page URL
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: dict) -> None:
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _route(self, method: str) -> None:
            path = urlparse(self.path).path
            if method == "GET" and path == "/":
                return self._send(200, page, "text/html; charset=utf-8")
            fn = ROUTES.get((method, path))
            if not fn:
                return self._json(404, {"error": "not found"})
            # Custom header (not a query param): cross-site forms and plain fetches can't set it.
            if not secrets.compare_digest(self.headers.get("X-Tvlean-Token", ""), token):
                return self._json(403, {"error": "bad or missing token; open the URL tvlean printed"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                if not isinstance(body, dict):
                    raise ApiError(400, "body must be a JSON object")
                if method == "POST" and path != "/api/connect" and not body.get("device"):
                    raise ApiError(400, "device required")
                with _device_lock:
                    return self._json(200, fn(body))
            except ApiError as e:
                return self._json(e.status, {"error": str(e)})
            except (AdbError, ProfileError, engine.ApplyFailed) as e:
                return self._json(500, {"error": str(e)})
            except json.JSONDecodeError:
                return self._json(400, {"error": "invalid JSON"})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765, token: str | None = None) -> None:
    token = token or secrets.token_urlsafe(18)
    httpd = ThreadingHTTPServer((host, port), make_handler(token))
    shown = "localhost" if host in ("127.0.0.1", "::1") else host
    print(f"tvlean web UI: http://{shown}:{httpd.server_address[1]}/#token={token}", flush=True)
    if host not in ("127.0.0.1", "::1", "localhost"):
        print("Listening beyond localhost: anyone who can reach this address and has the token can change your TV.")
    print("Ctrl+C to stop.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

