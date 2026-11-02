#!/usr/bin/env bash
# Compila GwebBar e crea GwebBar.app (firma ad-hoc, solo uso personale).
#   ./build-app.sh            # crea build/GwebBar.app
#   ./build-app.sh --install  # e lo copia in ~/Applications, poi lo avvia
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
swift build -c release --arch arm64 --arch x86_64
BIN="$(swift build -c release --arch arm64 --arch x86_64 --show-bin-path)/GwebBar"

APP="build/GwebBar.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$BIN" "$APP/Contents/MacOS/GwebBar"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>GwebBar</string>
  <key>CFBundleDisplayName</key><string>Briefing</string>
  <key>CFBundleIdentifier</key><string>local.agency-briefing.gwebbar</string>
  <key>CFBundleExecutable</key><string>GwebBar</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <!-- Solo icona nella barra dei menu: niente Dock, niente menu applicazione -->
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST
codesign --force --sign - "$APP"
echo "Creato $APP"

if [[ "${1:-}" == "--install" ]]; then
  mkdir -p "$HOME/Applications"
  pkill -x GwebBar 2>/dev/null || true
  rm -rf "$HOME/Applications/GwebBar.app"
  cp -R "$APP" "$HOME/Applications/"
  open "$HOME/Applications/GwebBar.app"
  echo "Installato in ~/Applications/GwebBar.app e avviato"
fi
