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


def _selection(args, survey: engine.Survey) -> dict[str, str]:
    """Defaults, then each --with ID[=VALUE], minus each --without ID."""
    selection = {} if args.no_defaults else survey.defaults()
    for pick in args.with_ or []:
        oid, _, vid = pick.partition("=")
        opt = survey.option(oid)
        if opt is None:
            sys.exit(f"unknown option {oid!r}; `tvlean audit` lists them")
        if not vid:
            if opt.option.is_choice:
                ids = ", ".join(v.value.id for v in opt.values)
                sys.exit(f"{oid} is a choice; pick one with --with {oid}=VALUE ({ids})")
            vid = opt.option.values[0].id
        selection[oid] = vid
    for oid in args.without or []:
        if survey.option(oid) is None:
            sys.exit(f"unknown option {oid!r}; `tvlean audit` lists them")
        selection.pop(oid, None)
    return selection


STATUS_TEXT = {
    engine.Status.CHANGE: {"off": "will turn off", "on": "will turn on", "set": "will change"},
    engine.Status.DONE: {"off": "already off", "on": "already on", "set": "already set"},
    engine.Status.ABSENT: {"off": "not on this TV", "on": "not on this TV", "set": "not on this TV"},
}


def _status_text(o: engine.OptionStatus) -> str:
    if o.option.is_choice:
        now = o.value(o.current).value.label if o.current else "custom"
        picks = ", ".join(
            v.value.id + ("*" if v.value.id == o.option.recommended else "")
            for v in o.values
            if v.status is not engine.Status.UNAVAILABLE
        )
        return f"now {now}; values: {picks}"
    vs = o.values[0]
    if vs.status is engine.Status.UNAVAILABLE:
        return f"unavailable: {vs.why}"
    return STATUS_TEXT[vs.status][vs.value.action]


def _print_plan(changes: list[engine.Change]) -> None:
    if not changes:
        print("Nothing to change.")
        return
    for c in changes:
        verb = {"package": "turn off", "accessibility": "turn on"}.get(c.kind, f"set {c.before} -> {c.after}")
        print(f"  - {c.label or c.target} ({verb}, {c.target})")
        if c.reason:
            print(f"    {c.reason}")


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
        print(f"{p.id}\t{p.name}\tsections: {', '.join(s.id for s in p.sections)}")


def cmd_audit(args) -> None:
    dev = _device(args)
    prof = _profile(args, dev)
    mem = dev.mem_available_kb()
    print(f"Device:   {dev.model()} (Android {dev.prop('ro.build.version.release')})")
    print(f"Profile:  {prof.id}")
    print(f"Free RAM: {mem // 1024 if mem else '?'} MB")
    print(f"Home:     {dev.home_activity()} ({'ok' if engine.home_is_healthy(dev) else 'BROKEN'})")
    survey = engine.survey(dev, prof)
    for w in survey.warnings:
        print(f"WARNING:  {w}")
    defaults = survey.defaults()
    for sec in survey.sections:
        found = f" (found on this TV: {len(sec.options)})" if sec.section.detected else ""
        print(f"\n{sec.section.title}{found}")
        if args.verbose:
            print(f"  {sec.section.description}")
            if sec.section.caveat:
                print(f"  Good to know: {sec.section.caveat}")
        if sec.section.detected and not sec.options:
            print("  No known apps from this list are installed.")
        for o in sec.options:
            mark = "[x]" if o.option.id in defaults else "   "
            pick = f" -> {defaults[o.option.id]}" if o.option.is_choice and o.option.id in defaults else ""
            print(f"  {mark} {o.option.id:<16} {o.option.label:<40} {_status_text(o)}{pick}")
            if args.verbose and o.option.reason:
                print(f"      {o.option.reason}")
    n = len(survey.plan(defaults))
    print(f"\n[x] = picked by default ({n} change{'s' if n != 1 else ''}). Adjust with: tvlean apply --with ID[=VALUE] --without ID")


def cmd_apply(args) -> None:
    dev = _device(args)
    prof = _profile(args, dev)
    survey = engine.survey(dev, prof)
    try:
        changes = survey.plan(_selection(args, survey))
    except engine.SelectionError as e:
        sys.exit(f"error: {e}")
    print(f"Profile {prof.id}")
    _print_plan(changes)
    if not changes or args.dry_run:
        return
    if not args.yes and input("Apply these changes? [y/N] ").strip().lower() != "y":
        sys.exit("Aborted.")
    journal = engine.apply(dev, prof, changes)
    print(f"Applied {len(changes)} changes. Undo with: tvlean restore  (journal: {journal})")
    for pkg, hint in engine.verify_home_overrides(dev, prof, changes):
        print(f"\nNote: pressed Home but {pkg} didn't come up. {hint}")


def cmd_restore(args) -> None:
    dev = _device(args)
    path = Path(args.journal) if args.journal else engine.latest_journal(dev.serial)
    if not path or not path.exists():
        sys.exit("No journal to restore.")
    undone = engine.restore(dev, path)
    print(f"Restored {len(undone)} changes from {path}")


def cmd_serve(args) -> None:
    from .web import serve

    serve(args.host, args.port)


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
    p.add_argument("-v", "--verbose", action="store_true", help="explain each section and option")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("apply", help="apply a profile (journaled, reversible)")
    p.add_argument("-p", "--profile", help="profile id or path to a .toml")
    p.add_argument("--with", dest="with_", action="append", metavar="ID[=VALUE]", help="also change this option")
    p.add_argument("--without", action="append", metavar="ID", help="leave this option alone")
    p.add_argument("--no-defaults", action="store_true", help="start from nothing instead of the recommended picks")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    p.set_defaults(fn=cmd_apply)

    p = sub.add_parser("restore", help="undo the last apply (or a given journal)")
    p.add_argument("journal", nargs="?")
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("serve", help="open the web UI")
    p.add_argument("--host", default="127.0.0.1", help="address to listen on (default: localhost only)")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except (AdbError, ProfileError, engine.ApplyFailed) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
