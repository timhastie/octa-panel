#!/bin/bash
# Build "out/Virtual Panel.app" from VirtualPanel.swift with swiftc alone (no
# Xcode project, like tools/hw/rec.swift): compile, assemble the bundle, bake
# the repo root into Contents/Resources/repo_root, draw the icon, ad-hoc sign.
#
#   bash tools/panel/app/build.sh && open "out/Virtual Panel.app"
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
APP="$REPO/out/Virtual Panel.app"
BUILD="$REPO/out/_panel_app_build"
SWIFTC="${SWIFTC:-/Library/Developer/CommandLineTools/usr/bin/swiftc}"
[ -x "$SWIFTC" ] || SWIFTC=swiftc

# Explicit target and SDK: a shell running under Rosetta (bash launches
# x86_64 on this machine, 11 Sep 2026) makes swiftc default to x86_64 and
# lose its implicit SDK ("unable to load standard library"); 13.0 matches
# LSMinimumSystemVersion.
ARCH="${ARCH:-arm64}"
SDK="${SDKROOT:-$(xcrun --show-sdk-path)}"
mkdir -p "$BUILD"
"$SWIFTC" -O -target "$ARCH-apple-macosx13.0" -sdk "$SDK" -framework Cocoa -framework WebKit \
  "$HERE/VirtualPanel.swift" -o "$BUILD/VirtualPanel"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD/VirtualPanel" "$APP/Contents/MacOS/VirtualPanel"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
printf '%s\n' "$REPO" > "$APP/Contents/Resources/repo_root"

# Icon (optional): make_icon.py draws a PNG with the stdlib, sips scales the
# iconset, iconutil packs the .icns. A failure here leaves the generic icon.
PY="$REPO/.venv/bin/python3"; [ -x "$PY" ] || PY=python3
if "$PY" "$HERE/make_icon.py" "$BUILD/icon.png" 1024; then
  ICONSET="$BUILD/VirtualPanel.iconset"
  rm -rf "$ICONSET"; mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z "$s" "$s" "$BUILD/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z "$((s*2))" "$((s*2))" "$BUILD/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/VirtualPanel.icns"
else
  echo "icon: make_icon.py failed, keeping the generic icon" >&2
fi

codesign --force --deep -s - "$APP"
echo "built: $APP"
