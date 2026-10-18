import json
import os
import tempfile
import unittest
from pathlib import Path

from tvlean import engine
from tvlean.engine import SelectionError, Status
from tvlean.profiles import find_profile, parse

from fake import FakeDevice

ONN = find_profile("onn. Streaming Device 4K pro")
PROJECTIVY = "com.spocky.projengmenu"
SERVICE = PROJECTIVY + "/.services.ProjectivyAccessibilityService"
AMBIENT = {
    "secure/screensaver_enabled": "1",
    "secure/screensaver_components": "com.google.android.apps.tv.dreamx/.service.Backdrop",
}


def tiny():
    return parse({
        "profile": {"id": "t", "name": "T", "match_models": ["X"]},
        "section": {"apps": {"title": "Apps", "description": "d"}},
        "option": {
            "a": {"section": "apps", "label": "A", "reason": "r", "recommended": True, "disable": ["a.pkg"]},
            "b": {"section": "apps", "label": "B", "reason": "r", "recommended": True, "disable": ["b.pkg"]},
            "anim": {"section": "apps", "label": "Anim", "reason": "r", "recommended": True, "set": {"global/anim": "0.5"}},
        },
        "protected": [{"package": "home.launcher", "reason": "needed"}],
    })


class StateDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_STATE_HOME"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()
        del os.environ["XDG_STATE_HOME"]


class SurveyTests(unittest.TestCase):
    def status(self, dev, oid, vid="on"):
        return engine.survey(dev, ONN).option(oid).value(vid)

    def test_streaming_shows_only_installed_apps_and_never_picks_them(self):
        dev = FakeDevice({"com.netflix.ninja": "default", "com.google.android.youtube.tv": "disabled-user"})
        s = engine.survey(dev, ONN)
        streaming = next(sec for sec in s.sections if sec.section.id == "streaming")
        self.assertEqual([o.option.id for o in streaming.options], ["netflix", "youtube"])
        self.assertEqual(self.status(dev, "netflix").status, Status.CHANGE)
        self.assertEqual(self.status(dev, "youtube").status, Status.DONE)
        self.assertNotIn("netflix", s.defaults())

    def test_netflix_turns_off_its_helper_too_when_installed(self):
        dev = FakeDevice({"com.netflix.ninja": "default", "com.netflix.tokenmanager": "default"})
        targets = [c.target for c in engine.survey(dev, ONN).plan({"netflix": "on"})]
        self.assertEqual(targets, ["com.netflix.ninja", "com.netflix.tokenmanager"])

    def test_authored_options_stay_listed_when_absent(self):
        s = engine.survey(FakeDevice({}), ONN)
        self.assertEqual(s.option("talkback").values[0].status, Status.ABSENT)

    def test_projectivy_unavailable_until_installed(self):
        vs = self.status(FakeDevice({}), "projectivy")
        self.assertEqual(vs.status, Status.UNAVAILABLE)
        self.assertIn("Install Projectivy", vs.why)
        self.assertEqual(self.status(FakeDevice({PROJECTIVY: "default"}), "projectivy").status, Status.CHANGE)

    def test_unset_settings_read_as_the_stock_value(self):
        s = engine.survey(FakeDevice({}, {"global/window_animation_scale": "1.0"}), ONN)
        self.assertEqual(s.option("animations").current, "normal")

    def test_recommendation_applies_only_while_on_stock(self):
        self.assertEqual(engine.survey(FakeDevice({}), ONN).defaults().get("animations"), "fast")
        off = {k: "0" for k in ("global/window_animation_scale", "global/transition_animation_scale", "global/animator_duration_scale")}
        s = engine.survey(FakeDevice({}, off), ONN)
        self.assertEqual(s.option("animations").current, "off")
        self.assertNotIn("animations", s.defaults())

    def test_screensaver_has_no_default(self):
        s = engine.survey(FakeDevice({"com.android.dreams.basic": "default"}, AMBIENT), ONN)
        self.assertEqual(s.option("screensaver").current, "ambient")
        self.assertNotIn("screensaver", s.defaults())

    def test_simple_screensaver_needs_the_basic_dream(self):
        vs = engine.survey(FakeDevice({}, AMBIENT), ONN).option("screensaver").value("colors")
        self.assertEqual(vs.status, Status.UNAVAILABLE)

    def test_protected_catalog_app_is_unavailable(self):
        prof = parse({
            "profile": {"id": "t", "name": "T", "match_models": ["X"]},
            "section": {"streaming": {"catalog": "streaming"}},
            "protected": [{"package": "com.netflix.ninja", "reason": "the remote needs it"}],
        })
        vs = engine.survey(FakeDevice({"com.netflix.ninja": "default"}), prof).option("netflix").values[0]
        self.assertEqual(vs.status, Status.UNAVAILABLE)
        self.assertIn("the remote needs it", vs.why)

    def test_warns_when_a_protected_package_is_disabled(self):
        s = engine.survey(FakeDevice({"com.google.android.apps.tv.launcherx": "disabled-user"}), ONN)
        self.assertTrue(any("launcherx" in w for w in s.warnings))


class PlanTests(unittest.TestCase):
    def test_plan_follows_display_order_not_selection_order(self):
        dev = FakeDevice({"a.pkg": "default", "b.pkg": "default"})
        targets = [c.target for c in engine.survey(dev, tiny()).plan({"anim": "on", "b": "on", "a": "on"})]
        self.assertEqual(targets, ["a.pkg", "b.pkg", "global/anim"])

    def test_done_and_absent_picks_change_nothing(self):
        dev = FakeDevice({"a.pkg": "disabled-user"}, {"global/anim": "0.5"})
        self.assertEqual(engine.survey(dev, tiny()).plan({"a": "on", "b": "on", "anim": "on"}), [])

    def test_rejects_unknown_options_values_and_unavailable_picks(self):
        s = engine.survey(FakeDevice({}), ONN)
        for bad in ({"nope": "on"}, {"animations": "slow"}, {"projectivy": "on"}):
            with self.assertRaises(SelectionError):
                s.plan(bad)

    def test_launcher_override_turns_on_the_master_switch_once(self):
        dev = FakeDevice({PROJECTIVY: "default"})
        changes = engine.survey(dev, ONN).plan({"projectivy": "on"})
        self.assertEqual([(c.kind, c.target) for c in changes],
                         [("accessibility", SERVICE), ("setting", "secure/accessibility_enabled")])

    def test_choice_changes_every_setting_it_owns(self):
        changes = engine.survey(FakeDevice({}), ONN).plan({"animations": "off"})
        self.assertEqual({c.after for c in changes}, {"0"})
        self.assertEqual(len(changes), 3)


class ApplyTests(StateDirTest):
    def test_apply_restore_round_trip_and_second_plan_is_empty(self):
        dev = FakeDevice({"com.google.android.play.games": "default"}, {"secure/location_mode": "3"})
        before = (dict(dev.packages), dict(dev.settings))
        picks = {"play-games": "on", "location": "on", "animations": "fast"}
        journal = engine.apply(dev, ONN, engine.survey(dev, ONN).plan(picks))
        self.assertEqual(dev.packages["com.google.android.play.games"], "disabled-user")
        self.assertEqual(dev.settings["global/window_animation_scale"], "0.5")
        self.assertEqual(engine.survey(dev, ONN).plan(picks), [])

        engine.restore(dev, journal)
        self.assertEqual(dev.settings, before[1])
        self.assertIn(dev.packages["com.google.android.play.games"], ("default", "enabled"))

    def test_launcher_override_keeps_other_services_and_restore_removes_only_ours(self):
        other = "com.example/.Svc"
        dev = FakeDevice({PROJECTIVY: "default"}, {"secure/enabled_accessibility_services": other})
        journal = engine.apply(dev, ONN, engine.survey(dev, ONN).plan({"projectivy": "on"}))
        self.assertEqual(dev.settings["secure/enabled_accessibility_services"], f"{other}:{SERVICE}")
        engine.restore(dev, journal)
        self.assertEqual(dev.settings, {"secure/enabled_accessibility_services": other})

    def test_rolls_back_when_home_breaks(self):
        dev = FakeDevice({"a.pkg": "default", "b.pkg": "default"}, {"global/anim": "1.0"})
        dev.home_breakers = {"b.pkg"}
        with self.assertRaises(engine.ApplyFailed):
            engine.apply(dev, tiny(), engine.survey(dev, tiny()).plan({"a": "on", "b": "on", "anim": "on"}))
        self.assertEqual(dev.packages, {"a.pkg": "enabled", "b.pkg": "enabled"})
        self.assertEqual(dev.settings, {"global/anim": "1.0"})

    def test_rolls_back_when_a_command_fails(self):
        dev = FakeDevice({"a.pkg": "default", "b.pkg": "default"})
        dev.fail_on = "b.pkg"
        with self.assertRaises(RuntimeError):
            engine.apply(dev, tiny(), engine.survey(dev, tiny()).plan({"a": "on", "b": "on"}))
        self.assertEqual(dev.packages["a.pkg"], "enabled")

    def test_refuses_protected_package(self):
        dev = FakeDevice({"home.launcher": "default"})
        bad = [engine.Change("package", "home.launcher", "default", "disabled-user", "x")]
        with self.assertRaises(engine.ApplyFailed):
            engine.apply(dev, tiny(), bad)
        self.assertEqual(dev.packages["home.launcher"], "default")

    def test_journal_is_written_before_each_change(self):
        dev = FakeDevice({"a.pkg": "default"})
        path = Path(self.tmp.name) / "j.json"
        seen = []
        real_disable = dev.disable
        dev.disable = lambda p: (seen.append(path.exists()), real_disable(p))
        engine.apply(dev, tiny(), engine.survey(dev, tiny()).plan({"a": "on"}), journal=path)
        self.assertEqual(seen, [True])

    def test_old_journal_without_labels_still_restores(self):
        dev = FakeDevice({"a.pkg": "disabled-user"})
        path = Path(self.tmp.name) / "old.json"
        path.write_text(json.dumps({"serial": dev.serial, "profile": "t", "changes": [
            {"kind": "package", "target": "a.pkg", "before": "default", "after": "disabled-user", "reason": "r"}]}))
        engine.restore(dev, path)
        self.assertEqual(dev.packages["a.pkg"], "enabled")


class HomeOverrideTests(StateDirTest):
    def applied(self, toggle_on=True):
        dev = FakeDevice({PROJECTIVY: "default"})
        dev.home_override_toggle = toggle_on
        changes = engine.survey(dev, ONN).plan({"projectivy": "on"})
        engine.apply(dev, ONN, changes)
        return dev, changes

    def test_passes_when_the_launcher_takes_over(self):
        dev, changes = self.applied()
        self.assertEqual(engine.verify_home_overrides(dev, ONN, changes, settle=0), [])

    def test_reports_the_hint_when_the_in_app_switch_is_off(self):
        dev, changes = self.applied(toggle_on=False)
        failed = engine.verify_home_overrides(dev, ONN, changes, settle=0)
        self.assertEqual([pkg for pkg, _ in failed], [PROJECTIVY])
        self.assertIn("Override current launcher", failed[0][1])

    def test_never_presses_home_without_an_override_change(self):
        dev = FakeDevice({})
        self.assertEqual(engine.verify_home_overrides(dev, ONN, [], settle=0), [])
        self.assertIsNone(dev.foreground)


if __name__ == "__main__":
    unittest.main()
