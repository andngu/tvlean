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
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from .device import Device
from .profiles import A11Y_MASTER, Disable, EnableService, Option, Profile, Put, Section, Value

DISABLED_STATES = {"disabled", "disabled-user", "disabled-until-used"}

# Home resolving to any of these after a change means the device has no usable home screen.
BAD_HOME_MARKERS = ("setupwraith", "RecoveryActivity", "FallbackHome")

A11Y_LIST = ("secure", "enabled_accessibility_services")
MASTER_REASON = "Android's main switch for accessibility services. It has to be on for the service to run."


@dataclass
class Change:
    kind: str  # "package", "setting" or "accessibility"
    target: str  # package name, "namespace/key", or accessibility service
    before: str | None
    after: str | None
    reason: str
    label: str = ""  # friendly name; absent in journals written before labels existed


class Status(StrEnum):
    UNAVAILABLE = "unavailable"  # can't be picked here; `why` says what's missing
    ABSENT = "absent"  # nothing it would turn off is installed
    DONE = "done"  # the TV is already like this
    CHANGE = "change"  # picking it makes `changes`


@dataclass(frozen=True)
class ValueStatus:
    value: Value
    status: Status
    why: str = ""
    changes: tuple[Change, ...] = ()


@dataclass(frozen=True)
class OptionStatus:
    option: Option
    values: tuple[ValueStatus, ...]
    current: str | None  # id of the value the TV is on now; None for a toggle not yet done, or a custom setting
    default: str | None  # value id pre-picked for the user, or None

    def value(self, value_id: str) -> ValueStatus | None:
        return next((v for v in self.values if v.value.id == value_id), None)


@dataclass(frozen=True)
class SectionStatus:
    section: Section
    options: tuple[OptionStatus, ...]  # detected sections list only what's installed


class SelectionError(ValueError):
    pass


@dataclass(frozen=True)
class _Reading:
    installed: frozenset[str]
    states: dict[str, str | None]
    settings: dict[str, str | None]
    services: tuple[str, ...]
    master: str | None


@dataclass(frozen=True)
class Survey:
    """Everything tvlean can offer on one TV, from a single read. Pure after construction."""

    profile: Profile
    sections: tuple[SectionStatus, ...]
    warnings: tuple[str, ...]
    hidden: tuple[OptionStatus, ...] = ()  # catalog apps not on this TV: not shown, but picking one is a no-op

    def options(self) -> list[OptionStatus]:
        return [o for s in self.sections for o in s.options]

    def option(self, option_id: str) -> OptionStatus | None:
        return next((o for o in [*self.options(), *self.hidden] if o.option.id == option_id), None)

    def defaults(self) -> dict[str, str]:
        return {o.option.id: o.default for o in self.options() if o.default}

    def plan(self, selection: Mapping[str, str]) -> list[Change]:
        """The changes a selection makes, in display order, one per target.

        A selection maps option id to value id; leaving an option out leaves it alone.
        Picking something already done, or an app that isn't installed, changes nothing.
        """
        for oid, vid in selection.items():
            opt = self.option(oid)
            if opt is None:
                raise SelectionError(f"unknown option {oid!r}")
            vs = opt.value(vid)
            if vs is None:
                ids = ", ".join(v.value.id for v in opt.values)
                raise SelectionError(f"{oid} has no value {vid!r}; pick one of: {ids}")
            if vs.status is Status.UNAVAILABLE:
                raise SelectionError(f"{opt.option.label}: {vs.why}")
        changes: dict[str, Change] = {}
        for opt in self.options():
            if (vid := selection.get(opt.option.id)) is not None:
                for c in opt.value(vid).changes:
                    changes.setdefault(c.target, c)
        return list(changes.values())


def survey(device: Device, profile: Profile) -> Survey:
    return _assess(profile, _read(device, profile))


def _read(device: Device, profile: Profile) -> _Reading:
    installed = frozenset(device.installed_packages())
    packages = {p.package for p in profile.protected}
    settings: set[str] = set()
    for opt in profile.options():
        for value in opt.values:
            for e in value.effects:
                if isinstance(e, Disable):
                    packages.add(e.package)
                elif isinstance(e, Put):
                    settings.add(e.target)
    return _Reading(
        installed=installed,
        states={p: device.package_state(p) for p in sorted(packages & installed)},
        settings={t: device.get_setting(*t.split("/", 1)) for t in sorted(settings)},
        services=tuple(enabled_services(device)),
        master=device.get_setting(*A11Y_MASTER.split("/", 1)),
    )


def _assess(profile: Profile, r: _Reading) -> Survey:
    sections = []
    hidden: list[OptionStatus] = []
    for sec in profile.sections:
        options = [_option_status(opt, sec, profile, r) for opt in sec.options]
        if sec.detected:
            hidden += [o for o in options if not _installed_any(o.option, r)]
            options = [o for o in options if _installed_any(o.option, r)]
        sections.append(SectionStatus(sec, tuple(options)))
    warnings = tuple(
        f"Protected package {p.package} is disabled. {p.reason}"
        for p in profile.protected
        if r.states.get(p.package) in DISABLED_STATES
    )
    return Survey(profile, tuple(sections), warnings, tuple(hidden))


def _installed_any(opt: Option, r: _Reading) -> bool:
    return any(isinstance(e, Disable) and e.package in r.installed for v in opt.values for e in v.effects)


def _option_status(opt: Option, sec: Section, profile: Profile, r: _Reading) -> OptionStatus:
    values = tuple(_value_status(opt, v, sec, profile, r) for v in opt.values)
    current = next((vs.value.id for vs in values if vs.status is Status.DONE), None)
    return OptionStatus(opt, values, current, _default(opt, values))


def _default(opt: Option, values: tuple[ValueStatus, ...]) -> str | None:
    """Recommended values are pre-picked only where they'd change something, and a
    choice only while the TV is still on its stock value, so a user's own pick wins."""
    rec = next((vs for vs in values if vs.value.id == opt.recommended), None)
    if rec is None or rec.status is not Status.CHANGE:
        return None
    if opt.is_choice:
        stock = next((vs for vs in values if vs.value.stock), None)
        if stock is None or stock.status is not Status.DONE:
            return None
    return rec.value.id


def _value_status(opt: Option, value: Value, sec: Section, profile: Profile, r: _Reading) -> ValueStatus:
    for req in value.requires:
        if req.package not in r.installed:
            return ValueStatus(value, Status.UNAVAILABLE, req.if_missing)
    if all(isinstance(e, Disable) for e in value.effects) and not any(e.package in r.installed for e in value.effects):
        return ValueStatus(value, Status.ABSENT)
    if sec.detected:
        for e in value.effects:
            if isinstance(e, Disable) and (p := profile.is_protected(e.package)):
                return ValueStatus(value, Status.UNAVAILABLE, f"tvlean never turns this off on this TV: {p.reason}")
    label = f"{opt.label}: {value.label}" if opt.is_choice else opt.label
    reason = (value.detail or opt.reason) if opt.is_choice else opt.reason
    # An unset setting behaves like the value the TV ships with.
    stock = {e.target: e.value for e in opt.stock.effects if isinstance(e, Put)} if opt.stock else {}
    changes: list[Change] = []
    for e in value.effects:
        if isinstance(e, Disable):
            state = r.states.get(e.package)
            if e.package in r.installed and state not in DISABLED_STATES:
                changes.append(Change("package", e.package, state, "disabled-user", reason, label))
        elif isinstance(e, Put):
            current = r.settings.get(e.target)
            effective = current if current is not None else stock.get(e.target)
            if not _same(effective, e.value):
                changes.append(Change("setting", e.target, current, e.value, reason, label))
        else:
            if e.service not in r.services:
                changes.append(Change("accessibility", e.service, "off", "on", reason, label))
            if r.master != "1":
                changes.append(Change("setting", A11Y_MASTER, r.master, "1", MASTER_REASON, "Accessibility switch"))
    return ValueStatus(value, Status.CHANGE if changes else Status.DONE, changes=tuple(changes))


def _same(current: str | None, wanted: str) -> bool:
    """adb reports animation scales as "1.0" when a profile says "1"."""
    if current is None:
        return False
    try:
        return float(current) == float(wanted)
    except ValueError:
        return current == wanted


def enabled_services(device: Device) -> list[str]:
    raw = device.get_setting(*A11Y_LIST) or ""
    return [s for s in raw.split(":") if s]


def _set_services(device: Device, services: list[str]) -> None:
    device.put_setting(*A11Y_LIST, ":".join(services) or None)


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
    elif change.kind == "accessibility":
        # Append rather than overwrite: other services (e.g. a user's own) may already be on.
        services = enabled_services(device)
        if change.target not in services:
            _set_services(device, [*services, change.target])
    else:
        ns, key = change.target.split("/", 1)
        device.put_setting(ns, key, change.after)


def _undo(device: Device, change: Change) -> None:
    if change.kind == "package":
        if change.before not in DISABLED_STATES:
            device.enable(change.target)
    elif change.kind == "accessibility":
        _set_services(device, [s for s in enabled_services(device) if s != change.target])
    else:
        ns, key = change.target.split("/", 1)
        device.put_setting(ns, key, change.before)


def home_is_healthy(device: Device) -> bool:
    home = device.home_activity()
    return bool(home) and "/" in home and not any(m in home for m in BAD_HOME_MARKERS)


def verify_home_overrides(
    device: Device, profile: Profile, changes: list[Change], settle: float = 3
) -> list[tuple[str, str]]:
    """Press Home and check that any newly enabled launcher override actually took over.

    Returns (package, hint) for each override that didn't. The service being on
    isn't enough: launchers like Projectivy also need an in-app toggle that adb
    can't reach, so the only honest check is to press Home and look.
    """
    enabled = {c.target for c in changes if c.kind == "accessibility"}
    if any(c.target == A11Y_MASTER for c in changes):
        # The master switch alone can bring an already-listed override to life.
        enabled |= set(enabled_services(device))
    overrides = [e for e in profile.home_overrides() if e.service in enabled]
    if not overrides:
        return []
    device.press_home()
    time.sleep(settle)
    front = device.foreground_package()
    return [(e.package, e.home_hint) for e in overrides if front != e.package]


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
