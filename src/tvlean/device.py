"""Thin wrapper around adb for a single device.

Everything that touches the device goes through `Device.shell`, so tests can
swap in a fake by overriding that one method.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

# `dumpsys package` reports enabled=N; these are PackageManager's COMPONENT_ENABLED_STATE_* values.
ENABLED_STATES = {
    0: "default",
    1: "enabled",
    2: "disabled",
    3: "disabled-user",
    4: "disabled-until-used",
}


class AdbError(RuntimeError):
    pass


def adb_path() -> str:
    path = shutil.which("adb")
    if not path:
        raise AdbError("adb not found on PATH; install Android platform-tools")
    return path


def connect(address: str) -> str:
    out = subprocess.run([adb_path(), "connect", address], capture_output=True, text=True, timeout=20)
    return (out.stdout + out.stderr).strip()


def list_devices() -> list[tuple[str, str]]:
    """Return (serial, state) pairs from `adb devices`."""
    out = subprocess.run([adb_path(), "devices"], capture_output=True, text=True, timeout=20).stdout
    rows = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


@dataclass
class Device:
    serial: str

    def shell(self, cmd: str) -> str:
        out = subprocess.run(
            [adb_path(), "-s", self.serial, "shell", cmd],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if out.returncode != 0:
            raise AdbError(f"{cmd!r} failed: {(out.stderr or out.stdout).strip()}")
        return out.stdout.strip()

    # --- device info -------------------------------------------------------

    def prop(self, name: str) -> str:
        return self.shell(f"getprop {name}")

    def model(self) -> str:
        return self.prop("ro.product.model")

    def mem_available_kb(self) -> int | None:
        m = re.search(r"MemAvailable:\s+(\d+)", self.shell("cat /proc/meminfo"))
        return int(m.group(1)) if m else None

    def home_activity(self) -> str:
        out = self.shell(
            "cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME"
        )
        return out.splitlines()[-1].strip() if out else ""

    def foreground_package(self) -> str | None:
        out = self.shell("dumpsys activity activities")
        m = re.search(r"topResumedActivity=ActivityRecord\{\S+ u\d+ ([\w.]+)/", out)
        return m.group(1) if m else None

    def press_home(self) -> None:
        self.shell("input keyevent KEYCODE_HOME")

    # --- packages ----------------------------------------------------------

    def installed_packages(self) -> set[str]:
        return {line.removeprefix("package:") for line in self.shell("pm list packages").splitlines() if line}

    def package_state(self, pkg: str) -> str | None:
        """Return the enabled state name for this user, or None if not installed."""
        out = self.shell(f"dumpsys package {pkg}")
        if f"Package [{pkg}]" not in out:
            return None
        m = re.search(r"User 0:.*?enabled=(\d)", out)
        return ENABLED_STATES.get(int(m.group(1)), "default") if m else "default"

    def disable(self, pkg: str) -> None:
        self.shell(f"pm disable-user --user 0 {pkg}")

    def enable(self, pkg: str) -> None:
        self.shell(f"pm enable {pkg}")

    # --- settings ----------------------------------------------------------

    def get_setting(self, namespace: str, key: str) -> str | None:
        val = self.shell(f"settings get {namespace} {key}")
        return None if val in ("", "null") else val

    def put_setting(self, namespace: str, key: str, value: str | None) -> None:
        if value is None:
            self.shell(f"settings delete {namespace} {key}")
        else:
            self.shell(f"settings put {namespace} {key} {value}")
