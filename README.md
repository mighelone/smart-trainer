# Smart Trainer

Indoor cycling app for macOS, written in Python. It:
- controls an FTMS smart trainer (Zwift Hub, KICKR, Neo, …) in ERG, resistance or slope mode
- reads a Bluetooth power meter (Favero Assioma, …) and a heart-rate strap (Garmin HRM, …)
- can match the trainer to your **pedal** power
- records every session crash-safely and uploads it to intervals.icu

## Install

The app runs on your Mac because it talks to your Bluetooth devices. There are two ways to install it:

- **Mac app (Apple Silicon only):** download `SmartTrainer-<version>-macos-arm64.dmg` from
  [Releases](https://github.com/mighelone/smart-trainer/releases),
  drag **Smart Trainer** to Applications and open it. It opens in its own window; macOS asks for
  Bluetooth permission on the first scan.

  The app isn't notarized by Apple, so the first launch is blocked: click **Done**, then go to
  **System Settings → Privacy & Security**, scroll down and click **Open Anyway**. Or run
  `xattr -dr com.apple.quarantine "/Applications/Smart Trainer.app"` once.
  macOS may ask for Bluetooth permission again after an update.
- **Command line (with [uv](https://docs.astral.sh/uv/)):**
  ```bash
  uv tool install git+https://github.com/mighelone/smart-trainer             # browser UI
  uv tool install 'smart-trainer[app] @ git+https://github.com/mighelone/smart-trainer'  # + desktop window (--native)
  ```
  Bluetooth permission is then granted to your terminal app.

### Releasing
Bump `version` in `pyproject.toml`, commit, then push a tag `vX.Y.Z` (`vX.Y.Zrc1` for a
pre-release). `.github/workflows/release.yml` builds the Apple Silicon DMG and attaches it to
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
1. **Settings:** enter your FTP and your intervals.icu API key (intervals.icu → Settings → Developer Settings). The key is stored in the macOS Keychain.
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
5. **Finish:** writes a FIT file and (optionally) uploads it to intervals.icu. Failed uploads can be retried from **History**.

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
| `smart_trainer/sync/` | sync providers (`intervals.py`; Garmin later via the same `SyncProvider` interface) |
| `smart_trainer/ui/` | NiceGUI pages |

### Adding a device
Create a driver subclassing `BleakDevice`:
- set `SERVICE_UUID`, `ROLES` and `DRIVER`
- decorate it with `@register`
- call `self.emit(Reading(...))` with the device's readings

Then import it in `ble/scanner.py`. The core never needs to change.

## Tests
```bash
uv run pytest
```
