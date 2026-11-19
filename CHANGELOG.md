# Changelog

All notable changes to Smart Trainer are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

**The package version comes from this file:** it is the first `## [X.Y.Z]` heading below
(`[Unreleased]` is skipped). To release, rename `[Unreleased]` to the new version and date, then
push a matching tag `vX.Y.Z`. See [Releasing](README.md#releasing).

## [Unreleased]

## [0.3.0] - 2026-11-19

### Added
- In the desktop window (`--native`, and the packaged app) logs are saved to
  `logs/smart-trainer.log` in the app data folder, rotated at ~1 MB (the last five files are
  kept). In browser mode they are printed to the terminal as before.
- Mini overlay: a compact always-on-top window with the key ride metrics and controls, for riding
  while watching a video in another window. Toggle it from the header (and **Expand** back) during a
  ride, or start with `--mini`.
- Upload rides to **Strava**. You use your own Strava API application: enter its Client ID and
  Secret in **Settings → Strava**, then **Connect with Strava**. The secret and the token are kept
  in the macOS Keychain. Rides upload as indoor rides, and a ride Strava already has counts as
  uploaded.
- Modular connections: each service (intervals.icu, Strava, …) declares what it can do (upload
  rides, provide planned workouts, sync the profile). Adding one doesn't touch the engine or the pages.

### Changed
- Rides are uploaded to every connected service. Each service in **Settings → Connections** can be
  turned off or left out of the automatic upload.
- **History** shows the upload status per service, and **Upload** retries only where it's missing
  or failed.
- Settings are migrated automatically. Existing sessions keep their intervals.icu upload status.
- Split the app core (`Engine`) into focused components: `DeviceHub` (devices and roles),
  `Ride` (the ride in progress), `CalibrationService`, `SessionStore` (session files, FIT
  export, recovery) and `SyncService` (uploads and profile sync). `Engine` now only wires them
  together and runs the control loop. Its lifecycle is an async context manager
  (`async with engine:`) instead of `start()`/`shutdown()`.
- Sync goes through the connections, which are injected at startup
  (`Engine.from_settings(connections=...)`), so another platform can be added without touching
  the core.

## [0.2.0] - 2026-11-17

### Added
- Calibration from the **Devices** page, with step-by-step guidance, live progress and the result:
  - smart trainer spin-down over the FTMS Control Point (target speed, "stop pedalling" prompt,
    spin-down status);
  - power meter zero offset (Cycling Power "Start Offset Compensation", e.g. Favero Assioma),
    showing the offset the meter reports.
  Calibration is disabled during a ride, trainer control pauses during a spin-down, and `--fake`
  simulates both flows.
- Rider profile sync from intervals.icu: FTP (the Ride indoor FTP when set), weight, power and
  HR zones, LTHR and max HR. It runs at launch and from **Settings → Sync now**. Synced fields
  are read-only; turn the switch off to enter them by hand. Rides in progress are never changed.
- Zone targets in intervals.icu workouts use your own power zones.
- Power, target and heart-rate tiles on the ride screen show the current zone as a coloured
  accent and a `Z1`–`Z7` badge.

### Changed
- Redesigned UI: a consistent dark theme across all pages, and ride-screen metric tiles that
  share one layout (icon, label, value, unit, detail line) in an equal-width grid. Values stay
  white for readability; the current interval shows a progress bar and the next step.

## [0.1.0] - 2026-11-17

### Added
- Control FTMS smart trainers (Zwift Hub, KICKR, Neo, …) in ERG, resistance and slope mode.
- Read Bluetooth power meters (Favero Assioma, …) and heart-rate straps (Garmin HRM, …).
- Power matching: correct the trainer's ERG target so pedal power hits the target, with
  fallback to trainer power if the pedals drop out.
- Workouts from intervals.icu, built-in workouts, `.zwo` import and free ride, with auto-pause
  when you stop pedalling.
- Crash-safe recording to SQLite with Resume / Finish & save / Discard on the next launch.
- FIT export and upload to intervals.icu, with retry from History.
- Demo mode with simulated devices (`--fake`) and iPad/phone access over the LAN (`--lan`).
- Desktop window mode (`--native`, default in the Mac app).
- Mac app (Apple Silicon) as a DMG, built and attached to GitHub Releases by the release workflow.
- Installation guide ([INSTALL.md](INSTALL.md)).

[Unreleased]: https://github.com/mighelone/smart-trainer/compare/v0.2.0...HEAD
[0.1.0]: https://github.com/mighelone/smart-trainer/releases/tag/v0.1.0
[0.2.0]: https://github.com/mighelone/smart-trainer/releases/tag/v0.2.0
