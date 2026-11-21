#!/usr/bin/env bash
# Build packaging/icon.icns from packaging/icon.svg with the tools that ship with macOS.
# build_macos.sh runs this before PyInstaller, which picks the icon up in smart_trainer.spec.
set -euo pipefail
cd "$(dirname "$0")"

ICONSET=$(mktemp -d)/icon.iconset
mkdir -p "$ICONSET"
# The sizes iconutil expects in an .iconset: each base size and its @2x (Retina) version
for size in 16 32 128 256 512; do
    sips -s format png -z "$size" "$size" icon.svg --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -s format png -z "$double" "$double" icon.svg \
        --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o icon.icns
rm -rf "$(dirname "$ICONSET")"
echo "Built packaging/icon.icns"
