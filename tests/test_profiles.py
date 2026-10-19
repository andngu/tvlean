import unittest

from tvlean.profiles import Disable, ProfileError, builtin_profiles, find_profile, parse

ONN = "onn. Streaming Device 4K pro"


def profile(**override):
    data = {
        "profile": {"id": "t", "name": "T", "match_models": ["X"]},
        "section": {"apps": {"title": "Apps", "description": "d"}},
        "option": {
            "games": {"section": "apps", "label": "Games", "reason": "r", "recommended": True, "disable": ["a.games"]},
        },
    }
    data.update(override)
    return parse(data)


class ProfileTests(unittest.TestCase):
    def test_builtin_profiles_load(self):
        self.assertTrue(builtin_profiles())

    def test_onn_matches_its_model(self):
        self.assertEqual(find_profile(ONN).id, "onn-4k-pro")

    def test_onn_never_disables_protected_packages(self):
        prof = find_profile(ONN)
        authored = [o for s in prof.sections if not s.detected for o in s.options]
        disabled = {e.package for o in authored for v in o.values for e in v.effects if isinstance(e, Disable)}
        self.assertTrue(prof.is_protected("com.google.android.apps.tv.launcherx"))
        self.assertFalse(disabled & {p.package for p in prof.protected})

    def test_every_onn_option_is_explained(self):
        for o in find_profile(ONN).options():
            self.assertTrue(o.label, o.id)
            if o.section != "streaming":
                self.assertGreater(len(o.reason), 20, o.id)

    def test_streaming_comes_from_the_catalog_and_is_never_recommended(self):
        streaming = next(s for s in find_profile(ONN).sections if s.id == "streaming")
        self.assertTrue(streaming.detected)
        netflix = next(o for o in streaming.options if o.id == "netflix")
        self.assertEqual([e.package for e in netflix.values[0].effects], ["com.netflix.ninja", "com.netflix.tokenmanager"])
        self.assertTrue(all(o.recommended is None for o in streaming.options))

    def test_screensaver_and_animations_are_choices(self):
        prof = find_profile(ONN)
        opts = {o.id: o for o in prof.options()}
        self.assertEqual([v.id for v in opts["screensaver"].values], ["ambient", "colors", "off"])
        self.assertIsNone(opts["screensaver"].recommended)
        self.assertEqual(opts["animations"].recommended, "fast")
        self.assertEqual(opts["animations"].stock.id, "normal")

    def test_a_target_has_one_owner(self):
        with self.assertRaises(ProfileError):
            profile(option={
                "a": {"section": "apps", "label": "A", "reason": "r", "disable": ["same.pkg"]},
                "b": {"section": "apps", "label": "B", "reason": "r", "disable": ["same.pkg"]},
            })

    def test_toggle_recommended_must_be_a_bool(self):
        with self.assertRaises(ProfileError):
            profile(option={"a": {"section": "apps", "label": "A", "reason": "r", "recommended": "yes", "disable": ["p"]}})

    def test_recommended_choice_needs_a_real_value_and_a_stock_value(self):
        values = {"x": {"label": "X", "set": {"global/k": "1"}}, "y": {"label": "Y", "set": {"global/k": "2"}}}
        with self.assertRaises(ProfileError):
            profile(option={"c": {"section": "apps", "label": "C", "reason": "r", "recommended": "z", "value": values}})
        with self.assertRaises(ProfileError):
            profile(option={"c": {"section": "apps", "label": "C", "reason": "r", "recommended": "y", "value": values}})

    def test_options_cannot_write_accessibility_settings_directly(self):
        with self.assertRaises(ProfileError):
            profile(option={"a": {"section": "apps", "label": "A", "reason": "r",
                                  "set": {"secure/enabled_accessibility_services": "x/.Y"}}})

    def test_authored_option_cannot_disable_a_protected_package(self):
        with self.assertRaises(ProfileError):
            profile(protected=[{"package": "a.games", "reason": "needed"}])

    def choice(self, values):
        return profile(option={"c": {"section": "apps", "label": "C", "reason": "r", "value": values}})

    def test_choice_values_only_set_settings(self):
        with self.assertRaises(ProfileError):
            self.choice({"x": {"label": "X", "disable": ["p.one"]}, "y": {"label": "Y", "set": {"global/k": "2"}}})

    def test_choice_settings_must_be_shared_so_switching_undoes_them(self):
        with self.assertRaises(ProfileError):
            self.choice({"x": {"label": "X", "set": {"global/k": "1", "global/extra": "1"}},
                         "y": {"label": "Y", "set": {"global/k": "2"}}})

    def test_flags_must_be_real_booleans_and_tables_real_tables(self):
        bad = [
            {"c": {"section": "apps", "label": "C", "reason": "r", "value": {
                "x": {"label": "X", "stock": "false", "set": {"global/k": "1"}},
                "y": {"label": "Y", "set": {"global/k": "2"}}}}},
            {"c": {"section": "apps", "label": "C", "reason": "r", "value": "abc"}},
            {"c": {"section": "apps", "label": "C", "reason": "r", "enable_service": 1}},
        ]
        for option in bad:
            with self.assertRaises(ProfileError, msg=option):
                profile(option=option)

    def test_shell_metacharacters_are_rejected(self):
        for effect in ({"disable": ["foo; reboot"]}, {"set": {"global/k": "1; reboot"}}, {"set": {"global/k k": "1"}}):
            with self.assertRaises(ProfileError, msg=effect):
                profile(option={"a": {"section": "apps", "label": "A", "reason": "r", **effect}})

    def test_option_needs_a_defined_section(self):
        with self.assertRaises(ProfileError):
            profile(option={"a": {"section": "nope", "label": "A", "reason": "r", "disable": ["p"]}})


if __name__ == "__main__":
    unittest.main()
