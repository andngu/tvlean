"""Plan, apply and undo profile changes.

Every apply writes a journal of each change's *previous* state before making
it, so `restore` can put the device back exactly as it was, even after a
crash halfway through.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .device import Device
from .profiles import Profile

DISABLED_STATES = {"disabled", "disabled-user", "disabled-until-used"}

# Home resolving to any of these after a change means the device has no usable home screen.
BAD_HOME_MARKERS = ("setupwraith", "RecoveryActivity", "FallbackHome")


@dataclass
class Change:
    kind: str  # "package" or "setting"
    target: str  # package name, or "namespace/key"
    before: str | None
    after: str | None
    reason: str


def plan(device: Device, profile: Profile, groups: list[str]) -> list[Change]:
    wanted = set(groups)
    changes = []
    installed = device.installed_packages()
    for rule in profile.packages:
        if not wanted.intersection(rule.groups) or rule.package not in installed:
            continue
        state = device.package_state(rule.package)
        if state is None or state in DISABLED_STATES:
            continue
        changes.append(Change("package", rule.package, state, "disabled-user", rule.reason))
    for rule in profile.settings:
        if not wanted.intersection(rule.groups):
            continue
        current = device.get_setting(rule.namespace, rule.key)
        if current == rule.value:
            continue
        changes.append(Change("setting", f"{rule.namespace}/{rule.key}", current, rule.value, rule.reason))
    return changes


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "tvlean"


def journal_path(serial: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", serial)
    return state_dir() / safe / f"{time.strftime('%Y%m%d-%H%M%S')}.json"


def _write_journal(path: Path, serial: str, profile_id: str, done: list[Change]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"serial": serial, "profile": profile_id, "changes": [asdict(c) for c in done]}, indent=2))
    tmp.replace(path)


def _do(device: Device, change: Change) -> None:
    if change.kind == "package":
        device.disable(change.target)
    else:
        ns, key = change.target.split("/", 1)
        device.put_setting(ns, key, change.after)


def _undo(device: Device, change: Change) -> None:
    if change.kind == "package":
        if change.before not in DISABLED_STATES:
            device.enable(change.target)
    else:
        ns, key = change.target.split("/", 1)
        device.put_setting(ns, key, change.before)


def home_is_healthy(device: Device) -> bool:
    home = device.home_activity()
    return bool(home) and "/" in home and not any(m in home for m in BAD_HOME_MARKERS)


class ApplyFailed(RuntimeError):
    pass


def apply(device: Device, profile: Profile, changes: list[Change], journal: Path | None = None) -> Path:
    """Apply changes, journaling each one first. Rolls everything back if home breaks."""
    for c in changes:
        if c.kind == "package" and (p := profile.is_protected(c.target)):
            raise ApplyFailed(f"refusing to disable protected package {c.target}: {p.reason}")

    journal = journal or journal_path(device.serial)
    done: list[Change] = []
    try:
        for c in changes:
            # Record before acting, so a crash mid-change still leaves an undo entry.
            _write_journal(journal, device.serial, profile.id, [*done, c])
            _do(device, c)
            done.append(c)
        if not home_is_healthy(device):
            raise ApplyFailed(f"home screen broke (resolves to {device.home_activity()!r}); rolled back")
    except Exception:
        for c in reversed(done):
            _undo(device, c)
        journal.unlink(missing_ok=True)
        raise
    return journal


def load_journal(path: Path) -> tuple[str, list[Change]]:
    data = json.loads(path.read_text())
    return data["serial"], [Change(**c) for c in data["changes"]]


def latest_journal(serial: str) -> Path | None:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", serial)
    files = sorted((state_dir() / safe).glob("*.json"))
    return files[-1] if files else None


def restore(device: Device, path: Path) -> list[Change]:
    _, changes = load_journal(path)
    for c in reversed(changes):
        _undo(device, c)
    path.rename(path.with_suffix(".restored"))
    return changes
