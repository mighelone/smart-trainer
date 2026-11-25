# Changelog

All notable changes to Smart Trainer are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

**The package version comes from this file:** it is the first `## [X.Y.Z]` heading below
(`[Unreleased]` is skipped). To release, rename `[Unreleased]` to the new version and date, then
push a matching tag `vX.Y.Z`. See [Releasing](README.md#releasing).

## [Unreleased]

### Added
- **Zwift Click v2 as a shifter:** the Click's + and − buttons shift the virtual gears (turn on
  virtual shifting in **Settings → Ride** and ride in **Slope** mode). Scan on the **Devices**
  page and choose **Use as shifter** on the controller marked "reports both buttons": one
  controller of the pair forwards the other's button, so that one is all you need. The Click
  accepts one connection at a time, so close Zwift (or any other app using it) first.
- In **Resistance** mode (a free ride, or a workout with ERG off), the Click's + and − make the
  trainer 5 % harder or easier, with or without virtual shifting. The − and + buttons on the
  ride page and the - and + keys do the same, and the slider follows.
- **Virtual shifting** for a bike with a single cog (such as a Zwift Cog): in **Slope** mode,
  shift 15 virtual gears with the − and + buttons on the ride page, or the - and + keys. Gear 8
  is the gear your chain is really in, and each gear is about 10 % harder or easier. The
  trainer resists like the road you're riding would in that gear: at the same cadence a harder
  gear means riding faster, so more air drag on the flat and more force on climbs. Turn it on in
  **Settings → Ride**. It needs a speed reading from the trainer or a speed sensor to work on
  the flat.

## [0.4.1] - 2026-11-21

### Added
- The Mac app and its DMG have their own icon: a power dial in the ride screen's zone colours,
  instead of the generic Python one.

### Fixed
- Dragging the manual slider after switching to **Fixed ERG** or **Slope** no longer freezes the
  ride page. The slider also opens with the right range when the page loads while Fixed ERG or
  Slope is active.

## [0.4.0] - 2026-11-20

### Added
- **Interval cues:** 5 seconds before each workout step change, the app beeps twice and shows
  what comes next (e.g. "In 5 s · Sprint · 400 W for 00:30"), in the full app and in the mini
  overlay, so you can follow the workout while watching a video. Turn it off, or test the sound,
  in **Settings → Ride**. Browsers only play sound after you've clicked in the page (pressing
  **Start** is enough).
- **Cadence targets** from intervals.icu and `.zwo` workouts are shown in the step title
  ("High Cadence · 100 rpm"), the "Next" line and the interval cue ("… @ 100 rpm"). When you ride
  more than 10 rpm off the target for 5 seconds, the cadence tile turns amber with a LOW or HIGH
  badge and a "speed up"/"ease off" hint, and the status line (also on the mini overlay) shows
  "Cadence low: 78 / 100 rpm".
- **ERG spiral guard:** in ERG mode, when your cadence stays below 50 rpm for 3 seconds, the
  trainer is let go to a flat road instead of braking harder until you stall. The status line shows
  "ERG released: spin up to 70 rpm"; after 2 seconds at 70 rpm or more the workout target comes
  straight back. The workout clock, the recorded target and the FIT file are not affected. Turn it
  off in **Settings → Ride**.

### Changed
- The "Next" line of a workout shows the target with the current intensity applied.
- Rides record the **mean power of each second** instead of the last power reading in it. Power
  meters send 1–4 readings a second, so FIT files (and the live chart) are smoother and closer to
  the work actually done, which also makes NP more accurate.
- The live ride chart sends only the new points each second instead of the whole ride, so it
  stays light on long rides and over `--lan` (iPad, phone). Power and heart rate are drawn
  downsampled in the browser, keeping the peaks.
- The device chips in the header are no longer rebuilt every 2 seconds on every page: only a
  chip whose device changes state is updated.
- After **Skip**, the power trace stays where it was ridden instead of shifting to line up with
  the new workout time.
- **History** opens quickly however many rides you have: it shows the 30 most recent sessions
  with **Load more** for older ones, reads them in the background, and only re-reads sessions
  that changed since the last time.
- **Finish** no longer freezes the app while the FIT file of a long ride is written: the export
  runs in the background.

### Fixed
- Trainer command timing (the periodic resend and the pedal power matching's settle time) now
  uses a monotonic clock, so a system clock change or sleep/wake no longer disturbs it.
- The workout clock advances by the real time between engine ticks (capped at 5 s) instead of a
  fixed second, so a late tick no longer makes it drift. Resuming a crashed session restores the
  active time the same way.

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

[Unreleased]: https://github.com/mighelone/smart-trainer/compare/v0.4.1...HEAD
[0.1.0]: https://github.com/mighelone/smart-trainer/releases/tag/v0.1.0
[0.2.0]: https://github.com/mighelone/smart-trainer/releases/tag/v0.2.0
[0.3.0]: https://github.com/mighelone/smart-trainer/releases/tag/v0.3.0
[0.4.0]: https://github.com/mighelone/smart-trainer/releases/tag/v0.4.0
[0.4.1]: https://github.com/mighelone/smart-trainer/releases/tag/v0.4.1
