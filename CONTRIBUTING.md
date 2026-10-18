# Contributing

## Adding a device profile

Profiles live in `src/tvlean/profiles/<device-id>.toml`. Copy
`onn-4k-pro.toml` as a starting point.

1. Get the exact model string: `adb shell getprop ro.product.model`, and put it in `match_models`.
2. Add `[[package]]` rules **only for packages you have disabled on that
   device yourself** and used it afterwards (reboot, open your apps, press Home,
   use voice search if you keep it).
3. If disabling something broke the device, add it under `[[protected]]` with
   what happened. This list is as valuable as the disable list.
4. Fill in `tested_on` with the Android version, `ro.product.device` and the date.
5. Run the tests, then `tvlean audit -v --profile path/to/your.toml` against the device.

Rules of thumb:

- Never disable Play Services (`com.google.android.gms`), the Play Store, or the
  stock launcher on Google TV.
- Disable, don't uninstall. tvlean only uses `pm disable-user`, which is
  reversible and survives without root.
- Every rule needs a `reason` a non-technical person can understand.
- Anything a typical user might still want (a streaming app, voice search) goes in an opt-in group.

## Code

- No runtime dependencies; stdlib only.
- All device access goes through `tvlean.device.Device`. Tests replace it with
  `tests/fake.py`, so new logic should come with a fake-device test.
