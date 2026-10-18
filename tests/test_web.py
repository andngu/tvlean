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
            {"com.google.android.play.games": "default", "com.spocky.projengmenu": "default"},
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
        self.assertEqual(self.call("/api/apply", {"device": self.dev.serial, "groups": ["bloat"]}, token="wrong")[0], 403)
        self.assertEqual(self.dev.packages["com.google.android.play.games"], "default")

    def test_devices(self):
        status, data = self.call("/api/devices")
        self.assertEqual(status, 200)
        self.assertEqual([d["state"] for d in data["devices"]], ["device", "unauthorized"])

    def test_status_lists_pending_per_group(self):
        status, data = self.call("/api/status", {"device": self.dev.serial})
        self.assertEqual(status, 200)
        groups = {g["id"]: g for g in data["groups"]}
        self.assertEqual([c["target"] for c in groups["bloat"]["pending"]], ["com.google.android.play.games"])
        self.assertFalse(groups["launcher"]["default"])
        self.assertEqual(groups["bloat"]["title"], "Unused preinstalled apps")
        self.assertIn("TalkBack", groups["bloat"]["caveat"])
        items = {i["label"]: i for i in groups["bloat"]["items"]}
        self.assertEqual(items["Play Games"]["status"], "pending")
        self.assertEqual(items["TalkBack"]["status"], "absent")  # not installed on the fake
        speed = {i["label"]: i for i in groups["speed"]["items"]}
        self.assertEqual((speed["Window animations"]["before"], speed["Window animations"]["after"]), ("1.0", "0.5"))
        self.assertEqual(speed["In-app animations"]["status"], "pending")
        self.assertTrue(data["device"]["home_ok"])
        self.assertFalse(data["can_restore"])

    def test_done_groups_still_list_their_items(self):
        self.call("/api/apply", {"device": self.dev.serial, "groups": ["speed"]})
        groups = {g["id"]: g for g in self.call("/api/status", {"device": self.dev.serial})[1]["groups"]}
        self.assertEqual(groups["speed"]["pending"], [])
        self.assertEqual({i["status"] for i in groups["speed"]["items"]}, {"done"})
        self.assertEqual(len(groups["speed"]["items"]), 3)

    def test_launcher_items_include_master_switch(self):
        groups = {g["id"]: g for g in self.call("/api/status", {"device": self.dev.serial})[1]["groups"]}
        labels = [i["label"] for i in groups["launcher"]["items"]]
        self.assertEqual(labels, ["Projectivy Home-button override", "Accessibility switch"])

    def test_apply_and_restore_round_trip(self):
        status, data = self.call("/api/apply", {"device": self.dev.serial, "groups": ["bloat", "speed"]})
        self.assertEqual((status, data["applied"]), (200, 4))  # 1 package + 3 animation settings
        self.assertEqual(self.dev.packages["com.google.android.play.games"], "disabled-user")
        self.assertTrue(self.call("/api/status", {"device": self.dev.serial})[1]["can_restore"])

        status, data = self.call("/api/restore", {"device": self.dev.serial})
        self.assertEqual((status, data["restored"]), (200, 4))
        self.assertEqual(self.dev.settings, {"global/window_animation_scale": "1.0"})

    def test_apply_ignores_unknown_groups(self):
        status, data = self.call("/api/apply", {"device": self.dev.serial, "groups": ["nope"]})
        self.assertEqual(status, 400)

    def test_unready_device_rejected(self):
        self.assertEqual(self.call("/api/status", {"device": "other:5555"})[0], 409)
        self.assertEqual(self.call("/api/status", {})[0], 400)


if __name__ == "__main__":
    unittest.main()
