"""Device profiles: what tvlean offers to change on one TV model.

A profile is a TOML file of sections and options. An option is a list of
values, and a value is a list of effects (end states such as "this package is
off"). A toggle is an option with one value; a choice has two or more. See
`profiles/onn-4k-pro.toml` for the format and CONTRIBUTING.md for what a
profile needs before it is accepted.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path

NAMESPACES = ("global", "secure", "system")
TOGGLE = "on"  # the value id of a toggle's only value
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*\Z")  # no "=", so the CLI's ID=VALUE stays unambiguous
A11Y_LIST = "secure/enabled_accessibility_services"
A11Y_MASTER = "secure/accessibility_enabled"

EFFECT_KEYS = {"disable", "set", "enable_service", "takes_over_home", "home_hint", "requires", "if_missing"}
OPTION_KEYS = {"section", "label", "reason", "caveat", "recommended", "value"}
VALUE_KEYS = {"label", "detail", "stock"} | EFFECT_KEYS
APP_KEYS = {"label", "disable", "reason"}  # catalog apps can't be recommended or change settings


@dataclass(frozen=True)
class Disable:
    """End state: package disabled for user 0. Never uninstalled."""

    package: str

    @property
    def target(self) -> str:
        return self.package


@dataclass(frozen=True)
class Put:
    """End state: one settings value."""

    namespace: str
    key: str
    value: str

    @property
    def target(self) -> str:
        return f"{self.namespace}/{self.key}"


@dataclass(frozen=True)
class EnableService:
    """End state: an accessibility service is listed and Android's master switch is on."""

    service: str  # "package/.ServiceClass"
    takes_over_home: bool = False  # after apply, press Home and check this package came up
    home_hint: str = ""  # what to tell the user if it didn't

    @property
    def target(self) -> str:
        return self.service

    @property
    def package(self) -> str:
        return self.service.split("/", 1)[0]


Effect = Disable | Put | EnableService


@dataclass(frozen=True)
class Requirement:
    package: str
    if_missing: str  # shown as the reason the value is unavailable


@dataclass(frozen=True)
class Value:
    id: str
    label: str
    detail: str
    effects: tuple[Effect, ...]
    requires: tuple[Requirement, ...] = ()
    stock: bool = False  # how the TV ships: an unset setting reads as this value's

    @property
    def presses_home(self) -> bool:
        return any(isinstance(e, EnableService) and e.takes_over_home for e in self.effects)

    @property
    def action(self) -> str:
        """How the UI phrases this value's changes: "off", "on" or "set"."""
        if all(isinstance(e, Disable) for e in self.effects):
            return "off"
        return "on" if any(isinstance(e, EnableService) for e in self.effects) else "set"


@dataclass(frozen=True)
class Option:
    id: str
    section: str
    label: str
    reason: str
    caveat: str
    values: tuple[Value, ...]
    recommended: str | None  # value id picked by default, or None

    @property
    def is_choice(self) -> bool:
        return len(self.values) > 1

    @property
    def stock(self) -> Value | None:
        return next((v for v in self.values if v.stock), None)


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    description: str
    caveat: str
    options: tuple[Option, ...]
    detected: bool = False  # options come from a catalog; only the ones installed are shown


@dataclass(frozen=True)
class Protected:
    package: str
    reason: str


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    match_models: tuple[str, ...]
    tested_on: tuple[str, ...]
    sections: tuple[Section, ...]
    protected: tuple[Protected, ...]

    def matches(self, model: str) -> bool:
        return model.strip().lower() in (m.lower() for m in self.match_models)

    def is_protected(self, pkg: str) -> Protected | None:
        return next((p for p in self.protected if p.package == pkg), None)

    def options(self) -> Iterator[Option]:
        return (o for s in self.sections for o in s.options)

    def home_overrides(self) -> list[EnableService]:
        return [
            e
            for o in self.options()
            for v in o.values
            for e in v.effects
            if isinstance(e, EnableService) and e.takes_over_home
        ]


class ProfileError(ValueError):
    pass


def _field(table: dict, key: str, where: str):
    if key not in table:
        raise ProfileError(f"{where}: missing {key!r}")
    return table[key]


def _check_keys(table: dict, allowed: set[str], where: str) -> None:
    if unknown := sorted(set(table) - allowed):
        raise ProfileError(f"{where}: unknown key {unknown[0]!r}")


def _check_id(id: str, where: str) -> None:
    if not ID_RE.match(id):
        raise ProfileError(f"{where}: id must be lowercase letters, digits and dashes")


def _strings(value, where: str, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
        raise ProfileError(f"{where}: {key} must be a list of strings")
    return value


def _effects(table: dict, where: str) -> tuple[tuple[Effect, ...], tuple[Requirement, ...]]:
    effects: list[Effect] = [Disable(p) for p in _strings(table.get("disable", []), where, "disable")]
    sets = table.get("set", {})
    if not isinstance(sets, dict):
        raise ProfileError(f"{where}: set must be a table of \"namespace/key\" = \"value\"")
    for target, value in sets.items():
        ns, _, key = target.partition("/")
        if ns not in NAMESPACES or not key:
            raise ProfileError(f"{where}: setting must be one of {'/, '.join(NAMESPACES)}/ plus a key, got {target!r}")
        if target in (A11Y_LIST, A11Y_MASTER):
            raise ProfileError(f"{where}: use enable_service instead of setting {target} directly")
        if not isinstance(value, str):
            raise ProfileError(f"{where}: {target} must be a string, exactly as adb writes it")
        effects.append(Put(ns, key, value))
    pkg_missing = table.get("if_missing")
    requires = [Requirement(p, pkg_missing or f"{p} isn't on this TV.") for p in _strings(table.get("requires", []), where, "requires")]
    if service := table.get("enable_service"):
        if "/" not in service or ":" in service:
            raise ProfileError(f"{where}: enable_service must be 'package/.Class', got {service!r}")
        e = EnableService(service, bool(table.get("takes_over_home", False)), table.get("home_hint", ""))
        if e.takes_over_home and not e.home_hint:
            raise ProfileError(f"{where}: takes_over_home needs a home_hint for when the check fails")
        effects.append(e)
        requires.append(Requirement(e.package, pkg_missing or f"{e.package} isn't on this TV."))
    elif "takes_over_home" in table or "home_hint" in table:
        raise ProfileError(f"{where}: takes_over_home and home_hint go with enable_service")
    if not effects:
        raise ProfileError(f"{where}: needs disable, set or enable_service")
    return tuple(effects), tuple(requires)


def _option(oid: str, table: dict, where: str) -> Option:
    _check_id(oid, where)
    label = _field(table, "label", where)
    values_table = table.get("value")
    if values_table is None:
        _check_keys(table, OPTION_KEYS | EFFECT_KEYS, where)
        effects, requires = _effects(table, where)
        values = (Value(TOGGLE, label, "", effects, requires),)
        rec = table.get("recommended", False)
        if not isinstance(rec, bool):
            raise ProfileError(f"{where}: a toggle's recommended is true or false")
        recommended = TOGGLE if rec else None
    else:
        if effect_keys := sorted(set(table) & EFFECT_KEYS):
            raise ProfileError(f"{where}: {effect_keys[0]} goes on each [option.{oid}.value.*], not on a choice")
        _check_keys(table, OPTION_KEYS, where)
        if len(values_table) < 2:
            raise ProfileError(f"{where}: a choice needs two or more values; put a toggle's effects on the option itself")
        values = tuple(_value(vid, vt, f"{where}.value.{vid}") for vid, vt in values_table.items())
        if sum(v.stock for v in values) > 1:
            raise ProfileError(f"{where}: only one value can be stock")
        recommended = table.get("recommended")
        if recommended is not None and recommended not in {v.id for v in values}:
            raise ProfileError(f"{where}: recommended must be one of its value ids, got {recommended!r}")
        if recommended is not None and not any(v.stock for v in values):
            raise ProfileError(f"{where}: a recommended choice needs a stock value, so tvlean can tell the user hasn't changed it")
    return Option(oid, _field(table, "section", where), label, _field(table, "reason", where), table.get("caveat", ""), values, recommended)


def _value(vid: str, table: dict, where: str) -> Value:
    _check_id(vid, where)
    _check_keys(table, VALUE_KEYS, where)
    effects, requires = _effects(table, where)
    return Value(vid, _field(table, "label", where), table.get("detail", ""), effects, requires, bool(table.get("stock", False)))


def _catalog_section(sid: str, name: str, where: str) -> Section:
    entry = resources.files("tvlean") / "catalog" / f"{name}.toml"
    if not entry.is_file():
        raise ProfileError(f"{where}: no catalog named {name!r}")
    data = tomllib.loads(entry.read_text())
    source = f"catalog/{name}.toml"
    head = _field(data, "section", source)
    apps = []
    for aid, t in _field(data, "app", source).items():
        at = f"{source}: [app.{aid}]"
        _check_id(aid, at)
        _check_keys(t, APP_KEYS, at)
        label = _field(t, "label", at)
        disable = tuple(Disable(p) for p in _strings(_field(t, "disable", at), at, "disable"))
        apps.append(Option(aid, sid, label, t.get("reason", ""), "", (Value(TOGGLE, label, "", disable),), None))
    return Section(sid, _field(head, "title", source), _field(head, "description", source), head.get("caveat", ""), tuple(apps), True)


def parse(data: dict, source: str = "<profile>") -> Profile:
    _check_keys(data, {"profile", "section", "option", "protected"}, source)
    meta = _field(data, "profile", source)
    _check_keys(meta, {"id", "name", "match_models", "tested_on"}, f"{source}: [profile]")
    heads: dict[str, Section] = {}
    for sid, t in data.get("section", {}).items():
        where = f"{source}: [section.{sid}]"
        _check_id(sid, where)
        if "catalog" in t:
            _check_keys(t, {"catalog"}, where)
            heads[sid] = _catalog_section(sid, t["catalog"], where)
        else:
            _check_keys(t, {"title", "description", "caveat"}, where)
            heads[sid] = Section(sid, _field(t, "title", where), _field(t, "description", where), t.get("caveat", ""), ())
    authored: dict[str, list[Option]] = {sid: [] for sid, s in heads.items() if not s.detected}
    for oid, t in data.get("option", {}).items():
        where = f"{source}: [option.{oid}]"
        opt = _option(oid, t, where)
        if opt.section not in authored:
            raise ProfileError(f"{where}: section {opt.section!r} isn't a [section.*] with a title")
        authored[opt.section].append(opt)
    prof = Profile(
        id=_field(meta, "id", source),
        name=_field(meta, "name", source),
        match_models=tuple(_field(meta, "match_models", source)),
        tested_on=tuple(meta.get("tested_on", ())),
        sections=tuple(replace(s, options=tuple(authored[s.id])) if s.id in authored else s for s in heads.values()),
        protected=tuple(
            Protected(_field(p, "package", source), _field(p, "reason", source)) for p in data.get("protected", [])
        ),
    )
    validate(prof, source)
    return prof


def validate(prof: Profile, source: str) -> None:
    """Cross-option rules. Each target has one owner, so no two picks can conflict."""
    protected = {p.package for p in prof.protected}
    owner: dict[str, str] = {}
    ids: set[str] = set()
    for sec in prof.sections:
        if not sec.detected and not sec.options:
            raise ProfileError(f"{source}: [section.{sec.id}] has no options")
        for opt in sec.options:
            if opt.id in ids:
                raise ProfileError(f"{source}: option id {opt.id!r} is used twice (profile and catalog?)")
            ids.add(opt.id)
            for e in dict.fromkeys(e for v in opt.values for e in v.effects):
                if owner.setdefault(e.target, opt.id) != opt.id:
                    raise ProfileError(f"{source}: {e.target} is in both {owner[e.target]} and {opt.id}")
                # A catalog app may name a protected package; the survey marks it unavailable instead.
                if isinstance(e, Disable) and e.package in protected and not sec.detected:
                    raise ProfileError(f"{source}: {e.package} is both disabled by {opt.id} and protected")


def load_file(path: Path) -> Profile:
    with path.open("rb") as f:
        return parse(tomllib.load(f), str(path))


def builtin_profiles() -> list[Profile]:
    root = resources.files("tvlean") / "profiles"
    out = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".toml"):
            out.append(parse(tomllib.loads(entry.read_text()), entry.name))
    return out


def find_profile(model: str) -> Profile | None:
    return next((p for p in builtin_profiles() if p.matches(model)), None)
