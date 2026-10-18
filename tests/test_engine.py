import os
import tempfile
import unittest
from pathlib import Path

from tvlean import engine
from tvlean.profiles import ProfileError, builtin_profiles, find_profile, parse

from fake import FakeDevice

ONN = "onn. Streaming Device 4K pro"


def tiny_profile(**extra):
    data = {
        "profile": {"id": "t", "name": "T", "match_models": ["X"]},
        "groups": {"bloat": "b", "speed": "s"},
        "package": [
            {"package": "a.bloat", "reason": "r", "groups": ["bloat"]},
            {"package": "b.bloat", "reason": "r", "groups": ["bloat"]},
        ],
        "setting": [{"namespace": "global", "key": "anim", "value": "0.5", "reason": "r", "groups": ["speed"]}],
        "protected": [{"package": "home.launcher", "reason": "needed"}],
    }
    data.update(extra)
    return parse(data)


class ProfileTests(unittest.TestCase):
    def test_builtin_profiles_parse(self):
        self.assertTrue(builtin_profiles())

    def test_onn_matches_model(self):
        self.assertEqual(find_profile(ONN).id, "onn-4k-pro")

    def test_onn_never_disables_stock_launcher(self):
        prof = find_profile(ONN)
        disabled = {p.package for p in prof.packages}
        self.assertNotIn("com.google.android.apps.tv.launcherx", disabled)
        self.assertTrue(prof.is_protected("com.google.android.apps.tv.launcherx"))

    def test_rejects_package_both_disabled_and_protected(self):
        with self.assertRaises(ProfileError):
            tiny_profile(protected=[{"package": "a.bloat", "reason": "x"}])

    def test_rejects_unknown_group(self):
        with self.assertRaises(ProfileError):
            tiny_profile(package=[{"package": "a", "reason": "r", "groups": ["nope"]}])


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_STATE_HOME"] = self.tmp.name
        self.prof = tiny_profile()

    def tearDown(self):
        self.tmp.cleanup()
        del os.environ["XDG_STATE_HOME"]

    def test_plan_skips_missing_and_already_disabled(self):
        dev = FakeDevice({"a.bloat": "disabled-user"}, {"global/anim": "0.5"})
        self.assertEqual(engine.plan(dev, self.prof, ["bloat", "speed"]), [])

    def test_plan_respects_groups(self):
        dev = FakeDevice({"a.bloat": "default"}, {"global/anim": "1.0"})
        targets = [c.target for c in engine.plan(dev, self.prof, ["speed"])]
        self.assertEqual(targets, ["global/anim"])

    def test_apply_then_restore_round_trips(self):
        dev = FakeDevice({"a.bloat": "default", "b.bloat": "enabled"}, {})
        before = (dict(dev.packages), dict(dev.settings))
        changes = engine.plan(dev, self.prof, ["bloat", "speed"])
        journal = engine.apply(dev, self.prof, changes)
        self.assertEqual(dev.packages["a.bloat"], "disabled-user")
        self.assertEqual(dev.settings["global/anim"], "0.5")

        engine.restore(dev, journal)
        self.assertEqual(dev.settings, before[1])  # unset setting is deleted again, not left as "0.5"
        self.assertTrue(all(s in ("default", "enabled") for s in dev.packages.values()))
        self.assertIsNone(engine.latest_journal(dev.serial))

    def test_rolls_back_when_home_breaks(self):
        dev = FakeDevice({"a.bloat": "default", "b.bloat": "default"}, {"global/anim": "1.0"})
        dev.home_breakers = {"b.bloat"}
        changes = engine.plan(dev, self.prof, ["bloat", "speed"])
        with self.assertRaises(engine.ApplyFailed):
            engine.apply(dev, self.prof, changes)
        self.assertEqual(dev.packages, {"a.bloat": "enabled", "b.bloat": "enabled"})
        self.assertEqual(dev.settings, {"global/anim": "1.0"})

    def test_rolls_back_when_a_command_fails(self):
        dev = FakeDevice({"a.bloat": "default", "b.bloat": "default"})
        dev.fail_on = "b.bloat"
        with self.assertRaises(RuntimeError):
            engine.apply(dev, self.prof, engine.plan(dev, self.prof, ["bloat"]))
        self.assertEqual(dev.packages["a.bloat"], "enabled")

    def test_refuses_protected_package(self):
        dev = FakeDevice({"home.launcher": "default"})
        bad = [engine.Change("package", "home.launcher", "default", "disabled-user", "x")]
        with self.assertRaises(engine.ApplyFailed):
            engine.apply(dev, self.prof, bad)
        self.assertEqual(dev.packages["home.launcher"], "default")

    def test_journal_written_before_change(self):
        dev = FakeDevice({"a.bloat": "default"})
        path = Path(self.tmp.name) / "j.json"
        seen = []
        real_disable = dev.disable
        dev.disable = lambda p: (seen.append(path.exists()), real_disable(p))
        engine.apply(dev, self.prof, engine.plan(dev, self.prof, ["bloat"]), journal=path)
        self.assertEqual(seen, [True])


if __name__ == "__main__":
    unittest.main()
