"""Device profiles: which packages are safe to disable and which settings to change.

A profile is a TOML file. See `profiles/onn-4k-pro.toml` for the format and
CONTRIBUTING.md for what a profile needs before it is accepted.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

NAMESPACES = ("global", "secure", "system")


@dataclass
class Group:
    id: str
    title: str  # plain-English name shown in the UI
    description: str  # what the user will notice after applying
    caveat: str = ""  # what they give up, or anything to do first


@dataclass
class PackageRule:
    package: str
    label: str
    reason: str
    groups: list[str]

    kind = "package"

    @property
    def target(self) -> str:
        return self.package


@dataclass
class SettingRule:
    namespace: str
    key: str
    value: str
    label: str
    reason: str
    groups: list[str]

    kind = "setting"

    @property
    def target(self) -> str:
        return f"{self.namespace}/{self.key}"


@dataclass
class AccessibilityRule:
    """Turn on an app's accessibility service, e.g. a launcher's Home-button override."""

    service: str  # "package/.ServiceClass", as listed in enabled_accessibility_services
    label: str
    reason: str
    groups: list[str]
    takes_over_home: bool = False  # verify after apply that Home lands in this app
    hint: str = ""  # what to do if that verification fails

    kind = "accessibility"

    @property
    def target(self) -> str:
        return self.service

    @property
    def package(self) -> str:
        return self.service.split("/", 1)[0]


@dataclass
class Protected:
    package: str
    reason: str


@dataclass
class Profile:
    id: str
    name: str
    match_models: list[str]
    tested_on: list[str]
    groups: dict[str, Group]
    default_groups: list[str]
    packages: list[PackageRule] = field(default_factory=list)
    settings: list[SettingRule] = field(default_factory=list)
    accessibility: list[AccessibilityRule] = field(default_factory=list)
    protected: list[Protected] = field(default_factory=list)

    def matches(self, model: str) -> bool:
        return model.strip().lower() in (m.lower() for m in self.match_models)

    def is_protected(self, pkg: str) -> Protected | None:
        return next((p for p in self.protected if p.package == pkg), None)

    def rules_in(self, group: str) -> list[PackageRule | SettingRule | AccessibilityRule]:
        return [r for r in [*self.packages, *self.settings, *self.accessibility] if group in r.groups]


class ProfileError(ValueError):
    pass


def parse(data: dict, source: str = "<profile>") -> Profile:
    try:
        meta = data["profile"]
        groups = {
            gid: Group(gid, g["title"], g["description"], g.get("caveat", ""))
            for gid, g in data.get("groups", {}).items()
        }
        prof = Profile(
            id=meta["id"],
            name=meta["name"],
            match_models=list(meta["match_models"]),
            tested_on=list(meta.get("tested_on", [])),
            groups=groups,
            default_groups=list(meta.get("default_groups", groups.keys())),
            packages=[PackageRule(p["package"], p["label"], p["reason"], list(p["groups"])) for p in data.get("package", [])],
            settings=[
                SettingRule(s["namespace"], s["key"], str(s["value"]), s["label"], s["reason"], list(s["groups"]))
                for s in data.get("setting", [])
            ],
            accessibility=[
                AccessibilityRule(
                    a["service"],
                    a["label"],
                    a["reason"],
                    list(a["groups"]),
                    bool(a.get("takes_over_home", False)),
                    a.get("hint", ""),
                )
                for a in data.get("accessibility", [])
            ],
            protected=[Protected(p["package"], p["reason"]) for p in data.get("protected", [])],
        )
    except KeyError as e:
        raise ProfileError(f"{source}: missing field {e}") from None
    except TypeError:
        raise ProfileError(f"{source}: each [groups.<id>] must be a table with title and description") from None
    validate(prof, source)
    return prof


def validate(prof: Profile, source: str) -> None:
    known = set(prof.groups)
    for g in prof.default_groups:
        if g not in known:
            raise ProfileError(f"{source}: default group {g!r} is not defined in [groups]")
    for rule in [*prof.packages, *prof.settings, *prof.accessibility]:
        for g in rule.groups:
            if g not in known:
                raise ProfileError(f"{source}: unknown group {g!r}")
    for a in prof.accessibility:
        if "/" not in a.service or ":" in a.service:
            raise ProfileError(f"{source}: accessibility service must be 'package/.Class', got {a.service!r}")
    for s in prof.settings:
        if s.namespace not in NAMESPACES:
            raise ProfileError(f"{source}: bad namespace {s.namespace!r} for {s.key}")
    protected = {p.package for p in prof.protected}
    for p in prof.packages:
        if p.package in protected:
            raise ProfileError(f"{source}: {p.package} is both disabled and protected")


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
