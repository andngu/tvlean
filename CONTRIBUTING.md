# Contributing

## Adding a device profile

Profiles live in `src/tvlean/profiles/<device-id>.toml`. Copy
`onn-4k-pro.toml` as a starting point.

1. Get the exact model string with `adb shell getprop ro.product.model`, and
   put it in `match_models`.
2. Add a `[section.<id>]` for each group of options, with a `title` and a
   `description`. To list streaming apps, add `[section.streaming]` with
   `catalog = "streaming"`.
3. Add an `[option.<id>]` for each thing a person can pick. Add only options
   you applied on that device yourself and then used the device: reboot,
   open your apps, press Home, and try voice search if you keep it.
4. If changing something broke the device, add the package under
   `[[protected]]` with what happened. This list is as valuable as the options.
5. Fill in `tested_on` with the Android version, `ro.product.device`, and the date.
6. Run the tests, then run `tvlean audit -v --profile path/to/your.toml` against the device.

### Option format

A toggle puts its effects on the option:

```toml
[option.play-games]
section = "bloat"
label = "Play Games"
reason = "Google's game hub and achievements service."
recommended = true
disable = ["com.google.android.play.games"]
```

A choice lists values instead. Mark the value the device ships with as
`stock = true`. tvlean reads an unset setting as the stock value, and it
recommends a choice only while the device is on that value:

```toml
[option.animations]
section = "display"
label = "Animation speed"
reason = "How long menus and app switches take to animate."
recommended = "fast"

[option.animations.value.normal]
label = "Normal"
stock = true
set = { "global/window_animation_scale" = "1" }

[option.animations.value.fast]
label = "Fast"
set = { "global/window_animation_scale" = "0.5" }
```

The effects are `disable` (a list of packages), `set` (settings as
`"namespace/key" = "value"`), and `enable_service` (an accessibility service).
A value that needs another app adds `requires` and `if_missing`. tvlean
checks these rules when it loads a profile:

- Each package and setting belongs to exactly one option, so no two picks conflict.
- Options can't set `secure/enabled_accessibility_services` or
  `secure/accessibility_enabled` directly. Use `enable_service`, which
  handles both.
- An option can't disable a package listed under `[[protected]]`.

### Rules of thumb

- Never disable Play Services (`com.google.android.gms`), the Play Store, or the
  stock launcher on Google TV.
- Disable, don't uninstall. tvlean only uses `pm disable-user`, which is
  reversible and works without root.
- Give every option a short `label` (for example "Play Games", not the package
  name) and a `reason` a non-technical person can understand: what the thing
  does and why turning it off is fine. Add a `caveat` for anything they give up.
- Recommend only what nearly everyone wants. Streaming apps are never recommended.

### Adding a streaming app

Add an `[app.<id>]` table to `src/tvlean/catalog/streaming.toml` with a
`label` and its `disable` packages. Every device profile with a streaming
section picks it up. A wrong package name is harmless, because tvlean shows
only installed apps.

## Code

- No runtime dependencies; stdlib only.
- All device access goes through `tvlean.device.Device`. Tests replace it with
  `tests/fake.py`, so new logic should come with a fake-device test.
