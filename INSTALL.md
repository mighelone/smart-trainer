# Installing Smart Trainer

Smart Trainer runs on your Mac, because it connects directly to your Bluetooth trainer,
power meter and heart-rate strap. Pick one of the two ways below.

| | Mac app | Command line |
|---|---|---|
| For | Anyone | People comfortable with a terminal |
| Macs | Apple Silicon (M1 and later) | Apple Silicon and Intel |
| Opens in | Its own window | Your browser (or a window with `--native`) |

## Option A: Mac app

### 1. Download and install
1. Go to [Releases](https://github.com/mighelone/smart-trainer/releases) and download
   `SmartTrainer-<version>-macos-arm64.dmg` from the latest release.
2. Open the DMG and drag **Smart Trainer** onto the **Applications** folder.
3. Eject the DMG.

### 2. Allow it to open (first launch only)
The app isn't notarized by Apple, so macOS blocks it the first time. This is expected.
Use **one** of these, which only affect Smart Trainer:

**From System Settings (no terminal)**
1. Open **Smart Trainer** from Applications. macOS says it can't verify the app. Click **Done**.
2. Open **System Settings → Privacy & Security** and scroll down to
   *"Smart Trainer was blocked to protect your Mac"*.
3. Click **Open Anyway** and confirm with your password or Touch ID.

**From the terminal**

macOS only checks apps marked as downloaded from the internet. Remove that mark:
```bash
xattr -dr com.apple.quarantine "/Applications/Smart Trainer.app"
```
Then open the app normally. This also fixes the message *"Smart Trainer is damaged and can't be opened"*,
which is the same block with a different wording.

You need to do this again after installing each new version.

> Don't turn off Gatekeeper for the whole Mac (`sudo spctl --master-disable`). It removes the check
> for every app, and you only need it lifted for this one.

### 3. Allow Bluetooth
The first time you press **Scan**, macOS asks whether Smart Trainer may use Bluetooth. Click **Allow**.

If you clicked *Don't Allow*, or devices never show up, turn it on in
**System Settings → Privacy & Security → Bluetooth**. macOS may ask again after an update.

## Option B: Command line

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. Install Smart Trainer:
   ```bash
   uv tool install git+https://github.com/mighelone/smart-trainer
   ```
   Or, to also be able to open it in its own window:
   ```bash
   uv tool install 'smart-trainer[app] @ git+https://github.com/mighelone/smart-trainer'
   ```
3. Run it:
   ```bash
   smart-trainer            # opens http://127.0.0.1:8080 in your browser
   smart-trainer --native   # its own window (needs the [app] install)
   smart-trainer --fake     # demo with simulated devices, to try it without a trainer
   smart-trainer --lan      # also open the ride screen on an iPad/phone: http://<your-mac>.local:8080
   ```
4. **Bluetooth:** macOS asks for permission for your **terminal app** (Terminal, iTerm, …), not for
   Smart Trainer. Allow it, or turn it on in **System Settings → Privacy & Security → Bluetooth**.

To update later:
```bash
uv tool upgrade smart-trainer
```

## After installing
Follow [First setup](README.md#first-setup) in the README: enter your FTP and intervals.icu API key,
scan for your devices, then pick a workout and ride.

## Your data
Settings, rides and FIT files are kept in `~/Library/Application Support/SmartTrainer/`, and your
intervals.icu API key in the macOS Keychain. Both installs use the same data, and uninstalling the app
doesn't delete it.

## Uninstall
- **Mac app:** drag **Smart Trainer** from Applications to the Trash.
- **Command line:**
  ```bash
  uv tool uninstall smart-trainer
  ```
- **Data (optional):** delete `~/Library/Application Support/SmartTrainer/`. This also deletes your
  ride history.

## Troubleshooting
| Problem | Fix |
|---|---|
| *"can't be opened"* or *"is damaged"* | Do [step 2](#2-allow-it-to-open-first-launch-only) above |
| Devices don't appear when scanning | Check Bluetooth permission (step 3 / option B step 4). Wake the devices: pedal a few turns, put on the HR strap. Close other apps connected to them (Zwift, Garmin Connect, …), since a device usually accepts only one connection |
| *"Address already in use"* (command line) | Another program uses port 8080: `smart-trainer --port 8081` |
| The DMG won't run on an Intel Mac | The Mac app is Apple Silicon only; use Option B |
