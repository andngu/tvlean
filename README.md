# tvlean

Safe, reversible cleanup for Google TV / Android TV devices over adb.

Most "debloat your TV" advice is a forum post with 40 packages to disable and
no way back. tvlean takes the opposite approach:

- **Per-device profiles.** Only packages verified safe *on that model* are touched.
- **Protected list.** Packages known to brick the home screen (e.g. disabling the
  stock launcher on the onn 4K Pro) are refused, with the reason.
- **Journaled.** Every change's previous state is written down *before* it's made.
- **Self-checking.** If the home screen breaks after applying, everything is rolled back automatically.
- **One-command undo.** `tvlean restore`.
- **No root, no dependencies.** Python 3.11+ and adb.

## Quick start

1. On the TV: Settings → System → About → click **Android TV OS build** 7 times, then
   Settings → System → Developer options → turn on **USB debugging**.
2. Install [platform-tools](https://developer.android.com/tools/releases/platform-tools) (for `adb`) and tvlean:

   ```sh
   pip install git+https://github.com/andngu/tvlean
   ```

3. Connect (works over LAN or Tailscale), accept the prompt on the TV, then audit:

   ```sh
   tvlean devices --connect 192.168.1.50
   tvlean audit -v          # read-only: what would change
   tvlean apply --dry-run   # show the plan
   tvlean apply             # do it (asks first)
   tvlean restore           # undo the last apply
   ```

## Web UI

Prefer clicking to typing? `tvlean serve` opens a small local page that shows
the device's status, what each group would change, and Apply / Undo buttons.

```sh
tvlean serve                      # http://localhost:8765/#token=...
tvlean serve --host 100.x.y.z     # e.g. your Tailscale IP, to use it from your phone
```

Open the exact URL it prints: it carries a random per-session token, and
every action requires it, so other websites can't drive your TV. It listens
on localhost only unless you pass `--host`.

## Groups

Profiles split changes into groups. Defaults are applied unless you pass
`--without GROUP`; opt-in groups need `--with GROUP`.

| Group | Default | What you'll notice |
|---|---|---|
| `bloat` | yes | Unused preinstalled apps (Play Games, TalkBack, Print Spooler, Google Feedback) disappear and stop running in the background |
| `privacy` | yes | Android's ad-interest system (Privacy Sandbox), Google's feedback reporter and location are turned off |
| `speed` | yes | Menu and app-switching animations run twice as fast |
| `streaming` | opt-in | Preinstalled YouTube and Netflix are turned off |
| `launcher` | opt-in | [Projectivy Launcher](https://play.google.com/store/apps/details?id=com.spocky.projengmenu) replaces the Google TV home screen (see below) |

`tvlean audit -v` and the web UI explain every item in plain English before
anything changes.

## Supported devices

| Device | Profile | Tested |
|---|---|---|
| onn. Streaming Device 4K pro | `onn-4k-pro` | Android 14 |

Your device not listed? See [CONTRIBUTING.md](CONTRIBUTING.md); profiles are
small TOML files and are the most useful thing you can contribute.

## Replacing the launcher

The stock Google TV launcher is the heaviest thing on most boxes, but disabling
it can leave you with no home screen (on the onn 4K Pro, a setup "recovery"
screen takes over). tvlean never does that. Instead, `--with launcher` turns on
[Projectivy Launcher](https://play.google.com/store/apps/details?id=com.spocky.projengmenu)'s
accessibility service so it can intercept the Home button:

```sh
tvlean apply --with launcher
```

Projectivy also has an in-app switch that adb can't reach, so after applying,
tvlean presses Home and checks Projectivy actually came up. If it didn't, it
tells you to turn on *Projectivy → Settings → General → Override current launcher*.

## Tips that aren't in profiles (yet)

- **Ad ID:** Settings → Privacy → Ads → *Delete advertising ID*. It lives inside
  Play Services, so tvlean doesn't touch it.

## Development

```sh
PYTHONPATH=src:tests python3 -m unittest discover -s tests
```

Tests use an in-memory fake device; no TV needed.

## License

MIT
