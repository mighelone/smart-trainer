# Smart Trainer

Indoor cycling app for macOS, written in Python. It:
- controls an FTMS smart trainer (Zwift Hub, KICKR, Neo, …) in ERG, resistance or slope mode
- reads a Bluetooth power meter (Favero Assioma, …) and a heart-rate strap (Garmin HRM, …)
- can match the trainer to your **pedal** power
- records every session crash-safely and uploads it to intervals.icu and/or Strava

## Install

Download the Mac app (Apple Silicon) from
[Releases](https://github.com/mighelone/smart-trainer/releases), or install it from the command line:

```bash
uv tool install git+https://github.com/mighelone/smart-trainer
```

The Mac app isn't notarized by Apple, so macOS blocks its first launch. See **[INSTALL.md](INSTALL.md)**
for how to open it, Bluetooth permission, updating, uninstalling and troubleshooting.

### Releasing
The version comes from [CHANGELOG.md](CHANGELOG.md): rename `## [Unreleased]` to
`## [X.Y.Z] - YYYY-MM-DD`, commit, then push a matching tag `vX.Y.Z` (`vX.Y.Zrc1` for a
pre-release). The release build fails if the tag doesn't match the changelog version. `.github/workflows/release.yml` builds the Apple Silicon DMG and attaches it to
the GitHub Release. **Actions → Release → Run workflow** builds it as a downloadable run artifact
without releasing. To build locally: `packaging/build_macos.sh`.

Signing and notarization are optional and run only when the repository secrets `MACOS_CERT_P12`,
`MACOS_CERT_PASSWORD`, `CODESIGN_IDENTITY`, `NOTARY_KEY`, `NOTARY_KEY_ID` and `NOTARY_ISSUER_ID`
are set (needs an Apple Developer account). Optional app icon: `packaging/icon.icns`.

## Run (from source)

```bash
uv run smart-trainer          # opens http://127.0.0.1:8080
uv run smart-trainer --fake   # demo mode with simulated devices (separate data, no uploads)
uv run smart-trainer --native # desktop window (`uv sync --extra app`)
uv run smart-trainer --lan    # also reachable from an iPad/phone at http://<your-mac>.local:8080
```

The first time you scan, macOS asks for Bluetooth permission for your terminal app.
Turn it on in System Settings → Privacy & Security → Bluetooth.

## First setup
1. **Settings:** enter your intervals.icu API key (intervals.icu → Settings → Developer Settings). The key is stored in the macOS Keychain.
   Your FTP (the Ride indoor FTP when set), weight, and power and HR zones are then synced from intervals.icu at every launch and with **Sync now**.
   To enter FTP and weight by hand instead, turn off *Sync FTP, zones and weight from intervals.icu*.
2. **Devices:** wake up the devices (pedal a few turns, put on the strap), then press **Scan**.
   - Zwift Hub → *Use as trainer*
   - Assioma → *Use for power*
   - HRM → *Use for HR*
   Devices reconnect automatically on later launches.
3. **Workouts:** pick a planned intervals.icu workout, a built-in one, import a `.zwo`, or choose free ride.
4. **Ride:** press **Start**.
   - The workout auto-pauses when you stop pedalling.
   - **ERG** switch: off means resistance mode with the target shown only as a guide.
   - **Power source** *Pedals*: the trainer's ERG target is corrected so your Assioma power hits the target. If the pedals drop out, the app falls back to trainer power.
5. **Finish:** writes a FIT file and (optionally) uploads it to every connected service (intervals.icu, Strava).
   **History** shows the upload status per service, and **Upload** retries only where it's missing or failed.

## Connections
**Settings → Connections** has a card for each service. Each one can be turned off and can opt out of
the automatic upload after a ride. The *Upload rides automatically when finished* switch turns automatic
uploads on or off for all of them.

| Service | Planned workouts | Profile sync | Ride upload |
|---|---|---|---|
| intervals.icu | ✓ | ✓ | ✓ (paired with the planned workout) |
| Strava | | | ✓ (as an indoor ride) |

### Strava
Strava needs an API application of your own (free, takes a minute):
1. Go to [strava.com/settings/api](https://www.strava.com/settings/api) and create an app. Set
   **Authorization Callback Domain** to `localhost`; the other fields can be anything.
2. In **Settings → Strava**, enter the app's **Client ID** and **Client Secret**, then press
   **Connect with Strava**.
3. Authorize the app in the browser window that opens. The page tells you when it's connected.

The Client Secret and the authorization token are stored in the macOS Keychain. **Disconnect**
forgets the token. Do the connection step on the Mac running the app, because Strava redirects
back to `127.0.0.1`. A ride Strava already has (a duplicate) counts as uploaded.

## Calibration
On **Devices**, press **Calibrate** on a connected device (not available during a ride):
- **Smart trainer: spin-down** (FTMS). Warm up for ~10 minutes, pedal up to the target speed the
  trainer asks for, then stop pedalling when told and let the flywheel coast. Trainers that
  calibrate themselves (or only through their own app) report that they don't support it.
- **Power meter: zero offset** (Cycling Power "Start Offset Compensation", e.g. Assioma). Unclip,
  put the cranks vertical with no weight on the pedals, keep still, then press **Start**. The
  offset reported by the meter is shown.

Trainer control is paused during a spin-down, and rides can't start until it finishes.
`--fake` simulates both flows.

## Crash recovery
Samples are written to SQLite (WAL mode) every second in
`~/Library/Application Support/SmartTrainer/sessions/`. If the app dies mid-ride, the next
launch offers **Resume** (continues the workout where it stopped), **Finish & save**, or **Discard**
(Discard moves the session to `sessions/discarded/`).

## Layout
| Path | What |
|---|---|
| `smart_trainer/ble/` | BLE parsers and drivers: `ftms.py`, `cycling_power.py`, `heart_rate.py`, `fake.py` |
| `smart_trainer/core/` | engine (1 Hz loop), source selection, trainer control and power matching, workouts, recorder |
| `smart_trainer/export/fit.py` | FIT activity export |
| `smart_trainer/sync/` | connections: `base.py` (interface), `registry.py`, `intervals.py`, `strava.py` |
| `smart_trainer/ui/` | NiceGUI pages; `connections.py` holds the connection settings cards and the OAuth callback |

### Adding a device
Create a driver subclassing `BleakDevice`:
- set `SERVICE_UUID`, `ROLES` and `DRIVER`
- decorate it with `@register`
- call `self.emit(Reading(...))` with the device's readings
- optionally implement `Calibratable` (`CALIBRATION` + `calibrate()`) to get a **Calibrate** button

Then import it in `ble/scanner.py`. The core never needs to change.

### Adding a connection
Create a module in `smart_trainer/sync/` with a `Connection` subclass:
- set `id`, `name` and `capabilities` (`UPLOAD`, `WORKOUTS`, `PROFILE`)
- implement `is_configured()`, `test()`, and the method of each capability: `upload_activity(ride)`,
  `list_workouts(...)` or `profile()`
- keep non-secret options in `self.options` and secrets in the keyring with `get_secret`/`set_secret`
- decorate the class with `@register`

Then import it in `sync/registry.py`. The engine, History and Workouts pages pick it up from its
capabilities. A settings card with enable/auto-upload switches appears automatically. For
connection-specific fields, add a renderer decorated with `@settings_renderer("<id>")` in
`ui/connections.py`.

## Tests
```bash
uv run pytest
```
