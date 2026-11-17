#!/usr/bin/env bash
# Build "Smart Trainer.app" and a DMG in dist/.
#
# Optional signing/notarization (all three needed for Gatekeeper-friendly builds):
#   CODESIGN_IDENTITY   e.g. "Developer ID Application: Your Name (TEAMID)"
#   NOTARY_KEY_PATH     path to the App Store Connect API key (.p8)
#   NOTARY_KEY_ID, NOTARY_ISSUER_ID
set -euo pipefail
cd "$(dirname "$0")/.."

APP="dist/Smart Trainer.app"
VERSION=$(uv run --no-project python packaging/version.py)  # from CHANGELOG.md
DMG="dist/SmartTrainer-${VERSION}-macos-$(uname -m).dmg"

uv sync --group package
uv run --no-sync pyinstaller --noconfirm --clean packaging/smart_trainer.spec

if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
    echo "Signing with $CODESIGN_IDENTITY"
    codesign --force --deep --timestamp --options runtime \
        --entitlements packaging/entitlements.plist \
        --sign "$CODESIGN_IDENTITY" "$APP"
    codesign --verify --deep --strict --verbose=2 "$APP"
else
    echo "CODESIGN_IDENTITY not set: ad-hoc signing (works on this Mac only)"
    codesign --force --deep --sign - --entitlements packaging/entitlements.plist "$APP"
fi

rm -f "$DMG"
STAGING=$(mktemp -d)
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
# hdiutil sometimes fails with "Resource busy" on CI runners; retry a few times
for attempt in 1 2 3 4 5; do
    if hdiutil create -volname "Smart Trainer" -srcfolder "$STAGING" -ov -format UDZO "$DMG"; then
        break
    fi
    [[ $attempt == 5 ]] && { echo "hdiutil failed" >&2; exit 1; }
    echo "hdiutil failed (attempt $attempt), retrying..." >&2
    sleep $((attempt * 5))
done
rm -rf "$STAGING"

if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
    codesign --force --timestamp --sign "$CODESIGN_IDENTITY" "$DMG"
    if [[ -n "${NOTARY_KEY_PATH:-}" ]]; then
        xcrun notarytool submit "$DMG" --wait \
            --key "$NOTARY_KEY_PATH" --key-id "$NOTARY_KEY_ID" --issuer "$NOTARY_ISSUER_ID"
        xcrun stapler staple "$DMG"
    fi
fi
echo "Built $DMG"
