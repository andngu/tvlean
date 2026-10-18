"""tvlean command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, engine
from .device import AdbError, Device, connect, list_devices
from .profiles import Profile, ProfileError, builtin_profiles, find_profile, load_file


def _device(args) -> Device:
    if args.device:
        if ":" in args.device and (args.device, "device") not in list_devices():
            print(connect(args.device), file=sys.stderr)
        return Device(args.device)
    ready = [s for s, state in list_devices() if state == "device"]
    if len(ready) != 1:
        sys.exit(f"found {len(ready)} ready devices; pass --device HOST:PORT or a serial")
    return Device(ready[0])


def _profile(args, device: Device) -> Profile:
    if args.profile:
        path = Path(args.profile)
        if path.exists():
            return load_file(path)
        match = next((p for p in builtin_profiles() if p.id == args.profile), None)
        if not match:
            sys.exit(f"unknown profile {args.profile!r}; see `tvlean profiles`")
        return match
    model = device.model()
    prof = find_profile(model)
    if not prof:
        sys.exit(f"no profile for model {model!r}; pass --profile, or contribute one (CONTRIBUTING.md)")
    return prof


def _groups(args, prof: Profile) -> list[str]:
    groups = list(prof.default_groups)
    for g in args.with_ or []:
        if g not in prof.groups:
            sys.exit(f"unknown group {g!r}; this profile has: {', '.join(prof.groups)}")
        groups.append(g)
    return [g for g in groups if g not in (args.without or [])]


def _print_plan(changes: list[engine.Change]) -> None:
    if not changes:
        print("Nothing to change.")
        return
    for c in changes:
        verb = "disable" if c.kind == "package" else f"set {c.before!r} -> {c.after!r}"
        print(f"  {verb:<24} {c.target}\n  {'':<24} {c.reason}")


def cmd_devices(args) -> None:
    rows = list_devices()
    if not rows:
        print("No devices. Try: tvlean devices --connect HOST[:5555]")
    for serial, state in rows:
        print(f"{serial}\t{state}")
    if args.connect:
        print(connect(args.connect if ":" in args.connect else f"{args.connect}:5555"))


def cmd_profiles(args) -> None:
    for p in builtin_profiles():
        print(f"{p.id}\t{p.name}\tgroups: {', '.join(p.groups)}")


def cmd_audit(args) -> None:
    dev = _device(args)
    prof = _profile(args, dev)
    mem = dev.mem_available_kb()
    print(f"Device:   {dev.model()} (Android {dev.prop('ro.build.version.release')})")
    print(f"Profile:  {prof.id}")
    print(f"Free RAM: {mem // 1024 if mem else '?'} MB")
    print(f"Home:     {dev.home_activity()} ({'ok' if engine.home_is_healthy(dev) else 'BROKEN'})")
    installed = dev.installed_packages()
    for p in prof.protected:
        if p.package in installed and dev.package_state(p.package) in engine.DISABLED_STATES:
            print(f"WARNING:  protected package {p.package} is disabled. {p.reason}")
    print()
    for group, desc in prof.groups.items():
        changes = engine.plan(dev, prof, [group])
        default = "" if group in prof.default_groups else " (opt-in)"
        print(f"[{group}]{default} {desc}: {len(changes)} pending")
        if changes and args.verbose:
            _print_plan(changes)


def cmd_apply(args) -> None:
    dev = _device(args)
    prof = _profile(args, dev)
    groups = _groups(args, prof)
    changes = engine.plan(dev, prof, groups)
    print(f"Profile {prof.id}, groups: {', '.join(groups)}")
    _print_plan(changes)
    if not changes or args.dry_run:
        return
    if not args.yes and input("Apply these changes? [y/N] ").strip().lower() != "y":
        sys.exit("Aborted.")
    journal = engine.apply(dev, prof, changes)
    print(f"Applied {len(changes)} changes. Undo with: tvlean restore  (journal: {journal})")


def cmd_restore(args) -> None:
    dev = _device(args)
    path = Path(args.journal) if args.journal else engine.latest_journal(dev.serial)
    if not path or not path.exists():
        sys.exit("No journal to restore.")
    undone = engine.restore(dev, path)
    print(f"Restored {len(undone)} changes from {path}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="tvlean", description=__doc__)
    ap.add_argument("--version", action="version", version=f"tvlean {__version__}")
    ap.add_argument("-d", "--device", help="adb serial or HOST:PORT (auto-connects)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("devices", help="list adb devices")
    p.add_argument("--connect", metavar="HOST[:PORT]")
    p.set_defaults(fn=cmd_devices)

    sub.add_parser("profiles", help="list built-in profiles").set_defaults(fn=cmd_profiles)

    p = sub.add_parser("audit", help="show device state and pending changes (read-only)")
    p.add_argument("-p", "--profile", help="profile id or path to a .toml")
    p.add_argument("-v", "--verbose", action="store_true", help="list each pending change")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("apply", help="apply a profile (journaled, reversible)")
    p.add_argument("-p", "--profile", help="profile id or path to a .toml")
    p.add_argument("--with", dest="with_", action="append", metavar="GROUP", help="add an opt-in group")
    p.add_argument("--without", action="append", metavar="GROUP", help="skip a default group")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    p.set_defaults(fn=cmd_apply)

    p = sub.add_parser("restore", help="undo the last apply (or a given journal)")
    p.add_argument("journal", nargs="?")
    p.set_defaults(fn=cmd_restore)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except (AdbError, ProfileError, engine.ApplyFailed) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
