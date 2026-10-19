import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from tvlean import cli

from fake import FakeDevice


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_STATE_HOME"] = self.tmp.name
        self.dev = FakeDevice(
            {"com.google.android.play.games": "default", "com.netflix.ninja": "default"},
            {"global/window_animation_scale": "1.0"},
        )
        patch = mock.patch.object(cli, "_device", lambda args: self.dev)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()
        del os.environ["XDG_STATE_HOME"]

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(list(argv))
        return out.getvalue()

    def test_audit_marks_defaults_and_lists_detected_apps(self):
        out = self.run_cli("audit")
        self.assertRegex(out, r"\[x\] play-games .* will turn off")
        self.assertRegex(out, r"\n {6}netflix +Netflix +will turn off")
        self.assertIn("Streaming apps (found on this TV: 1)", out)
        self.assertRegex(out, r"\[x\] animations .* now Normal.* -> fast")

    def test_dry_run_changes_nothing(self):
        out = self.run_cli("apply", "--dry-run", "--with", "netflix")
        self.assertIn("Netflix (turn off, com.netflix.ninja)", out)
        self.assertEqual(self.dev.packages["com.netflix.ninja"], "default")

    def test_with_without_and_no_defaults_shape_the_selection(self):
        out = self.run_cli("apply", "--dry-run", "--no-defaults", "--with", "animations=off")
        self.assertNotIn("Play Games", out)
        self.assertIn("Animation speed: Off", out)
        out = self.run_cli("apply", "--dry-run", "--without", "play-games")
        self.assertNotIn("Play Games", out)
        self.assertIn("Animation speed: Fast", out)

    def test_apply_yes_applies_only_the_selection(self):
        self.run_cli("apply", "--yes", "--no-defaults", "--with", "play-games")
        self.assertEqual(self.dev.packages["com.google.android.play.games"], "disabled-user")
        self.assertEqual(self.dev.packages["com.netflix.ninja"], "default")
        self.assertEqual(self.dev.settings["global/window_animation_scale"], "1.0")

    def test_readme_style_picks_work_on_a_tv_without_those_apps(self):
        out = self.run_cli("apply", "--dry-run", "--with", "netflix", "--with", "prime-video")
        self.assertIn("Netflix (turn off", out)
        self.assertNotIn("Prime Video", out)

    def test_bad_picks_exit_with_a_message(self):
        for argv, msg in [
            (("--with", "animations"), "animations is a choice"),
            (("--with", "animations=slow"), "no value 'slow'"),
            (("--with", "nope"), "unknown option 'nope'"),
            (("--without", "nope"), "unknown option 'nope'"),
        ]:
            with self.assertRaises(SystemExit) as e:
                self.run_cli("apply", "--dry-run", *argv)
            self.assertIn(msg, str(e.exception.code))


if __name__ == "__main__":
    unittest.main()
