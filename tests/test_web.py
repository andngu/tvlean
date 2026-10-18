import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from tvlean import web

from fake import FakeDevice

TOKEN = "test-token"


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_STATE_HOME"] = self.tmp.name
        self.dev = FakeDevice(
            {"com.google.android.play.games": "default", "com.spocky.projengmenu": "default", "com.netflix.ninja": "default"},
            {"global/window_animation_scale": "1.0"},
        )
        self.patches = [
            mock.patch.object(web, "list_devices", lambda: [(self.dev.serial, "device"), ("other:5555", "unauthorized")]),
            mock.patch.object(web, "Device", lambda serial: self.dev),
        ]
        for p in self.patches:
            p.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(TOKEN))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()
        del os.environ["XDG_STATE_HOME"]

    def call(self, path, body=None, token=TOKEN):
        req = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"X-Tvlean-Token": token, "Content-Type": "application/json"} if token else {},
            method="GET" if body is None else "POST",
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_page_is_served_with_csp(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"<title>tvlean</title>", r.read())
            self.assertIn("default-src 'none'", r.headers["Content-Security-Policy"])

    def test_api_requires_token(self):
        self.assertEqual(self.call("/api/devices", token=None)[0], 403)
        self.assertEqual(self.call("/api/devices", token="wrong")[0], 403)
        self.assertEqual(self.call("/api/apply", {"device": self.dev.serial, "selection": {"play-games": "on"}}, token="wrong")[0], 403)
        self.assertEqual(self.dev.packages["com.google.android.play.games"], "default")

    def test_devices(self):
        status, data = self.call("/api/devices")
        self.assertEqual(status, 200)
        self.assertEqual([d["state"] for d in data["devices"]], ["device", "unauthorized"])

    def status(self):
        status, data = self.call("/api/status", {"device": self.dev.serial})
        self.assertEqual(status, 200)
        return data

    def option(self, data, oid):
        return next(o for s in data["sections"] for o in s["options"] if o["id"] == oid)

    def test_status_describes_every_option_with_its_default(self):
        data = self.status()
        self.assertEqual([s["id"] for s in data["sections"]], ["bloat", "privacy", "streaming", "display", "launcher"])
        games = self.option(data, "play-games")
        self.assertEqual((games["kind"], games["default"], games["values"][0]["status"]), ("toggle", "on", "change"))
        self.assertEqual(games["values"][0]["changes"][0]["target"], "com.google.android.play.games")
        anim = self.option(data, "animations")
        self.assertEqual((anim["kind"], anim["current"], anim["default"]), ("choice", "normal", "fast"))
        self.assertTrue(data["device"]["home_ok"])
        self.assertFalse(data["can_restore"])

    def test_status_lists_only_installed_streaming_apps(self):
        streaming = next(s for s in self.status()["sections"] if s["id"] == "streaming")
        self.assertTrue(streaming["detected"])
        self.assertEqual([o["id"] for o in streaming["options"]], ["netflix"])
        self.assertIsNone(streaming["options"][0]["default"])

    def test_launcher_option_presses_home(self):
        value = self.option(self.status(), "projectivy")["values"][0]
        self.assertTrue(value["presses_home"])
        self.assertEqual([c["label"] for c in value["changes"]], ["Projectivy as your home screen", "Accessibility switch"])

    def test_apply_and_restore_round_trip(self):
        selection = {"play-games": "on", "animations": "fast"}
        status, data = self.call("/api/apply", {"device": self.dev.serial, "selection": selection})
        self.assertEqual((status, data["applied"]), (200, 4))
        self.assertEqual(self.dev.packages["com.google.android.play.games"], "disabled-user")
        self.assertEqual(self.dev.packages["com.netflix.ninja"], "default")
        self.assertTrue(self.status()["can_restore"])
        self.assertEqual(self.option(self.status(), "animations")["current"], "fast")

        status, data = self.call("/api/restore", {"device": self.dev.serial})
        self.assertEqual((status, data["restored"]), (200, 4))
        self.assertEqual(self.dev.settings, {"global/window_animation_scale": "1.0"})

    def test_apply_rejects_bad_selections(self):
        for selection in ({"nope": "on"}, {"animations": "slow"}, ["play-games"], {"play-games": True}):
            status, _ = self.call("/api/apply", {"device": self.dev.serial, "selection": selection})
            self.assertEqual(status, 400, selection)
        self.assertEqual(self.dev.packages["com.google.android.play.games"], "default")

    def test_unready_device_rejected(self):
        self.assertEqual(self.call("/api/status", {"device": "other:5555"})[0], 409)
        self.assertEqual(self.call("/api/status", {})[0], 400)


if __name__ == "__main__":
    unittest.main()
