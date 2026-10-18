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
   pip install git+https://github.com/<you>/tvlean
   ```

3. Connect (works over LAN or Tailscale), accept the prompt on the TV, then audit:

   ```sh
   tvlean devices --connect 192.168.1.50
   tvlean audit -v          # read-only: what would change
   tvlean apply --dry-run   # show the plan
   tvlean apply             # do it (asks first)
   tvlean restore           # undo the last apply
   ```

## Groups

Profiles split changes into groups. Defaults are applied unless you pass
`--without GROUP`; opt-in groups need `--with GROUP`.

| Group | Default | What |
|---|---|---|
| `bloat` | yes | Preinstalled apps nothing depends on (Play Games, TalkBack, print spooler, feedback) |
| `privacy` | yes | Privacy Sandbox ad/personalization services, location |
| `power` | yes | Turn off "Stay awake" so the box actually sleeps |
| `speed` | yes | 0.5x animations |
| `streaming` | opt-in | Preinstalled YouTube / Netflix |

## Supported devices

| Device | Profile | Tested |
|---|---|---|
| onn. Streaming Device 4K pro | `onn-4k-pro` | Android 14 |

Your device not listed? See [CONTRIBUTING.md](CONTRIBUTING.md); profiles are
small TOML files and are the most useful thing you can contribute.

## Tips that aren't in profiles (yet)

- **Lighter launcher:** on Google TV you can't safely disable the stock launcher.
  Install [Projectivy Launcher](https://play.google.com/store/apps/details?id=com.spocky.projengmenu)
  and turn on *Settings → General → Override current launcher* instead.
- **Ad ID:** Settings → Privacy → Ads → *Delete advertising ID*. It lives inside
  Play Services, so tvlean doesn't touch it.

## Development

```sh
PYTHONPATH=src:tests python3 -m unittest discover -s tests
```

Tests use an in-memory fake device; no TV needed.

## License

MIT
