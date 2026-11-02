#!/usr/bin/env bash
# Compila l'app Briefing (GwebBar) e crea Briefing.app con icona.
#   ./build-app.sh             # crea build/Briefing.app
#   ./build-app.sh --install   # e la installa in ~/Applications e la avvia
#   ./build-app.sh --dmg       # crea build/Briefing-<versione>.dmg da passare al team
#
# Firma: "Developer ID Application" se presente nel portachiavi (SIGN_IDENTITY per sceglierla),
# altrimenti ad-hoc. Notarizzazione del DMG se NOTARY_PROFILE è il nome di un profilo creato
# una volta con `xcrun notarytool store-credentials`.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
VERSION="1.0.0"
BUNDLE_ID="agency.gweb.briefing"
APP="build/Briefing.app"

swift build -c release --arch arm64 --arch x86_64
BIN="$(swift build -c release --arch arm64 --arch x86_64 --show-bin-path)/GwebBar"

# Icona: generata dal logo vettoriale (Sources/GwebBar/Logo.swift), stessa geometria della barra.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
swiftc -O -parse-as-library Sources/GwebBar/Logo.swift scripts/MakeIcon.swift -o "$WORK/mkicon"
"$WORK/mkicon" "$WORK/AppIcon.iconset" >/dev/null
iconutil -c icns "$WORK/AppIcon.iconset" -o "$WORK/AppIcon.icns"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/GwebBar"
cp "$WORK/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Briefing</string>
  <key>CFBundleDisplayName</key><string>Briefing</string>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundleExecutable</key><string>GwebBar</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHumanReadableCopyright</key><string>GWEB Agency</string>
  <!-- Solo icona nella barra dei menu: niente Dock, niente menu applicazione -->
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

IDENTITY="${SIGN_IDENTITY:-$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application:[^"]*\)".*/\1/p' | head -1)}"
if [[ -n "$IDENTITY" ]]; then
  codesign --force --options runtime --timestamp --sign "$IDENTITY" "$APP"
  echo "Firmata con: $IDENTITY"
else
  codesign --force --sign - "$APP"
  echo "Firma ad-hoc (nessun Developer ID): sugli altri Mac servirà 'Apri comunque'"
fi
codesign --verify --strict "$APP"
echo "Creato $APP ($VERSION)"

case "${1:-}" in
  --install)
    mkdir -p "$HOME/Applications"
    pkill -x GwebBar 2>/dev/null || true
    rm -rf "$HOME/Applications/GwebBar.app" "$HOME/Applications/Briefing.app"
    cp -R "$APP" "$HOME/Applications/"
    open "$HOME/Applications/Briefing.app"
    echo "Installata in ~/Applications/Briefing.app e avviata"
    ;;
  --dmg)
    STAGE="$WORK/dmg"
    mkdir -p "$STAGE"
    cp -R "$APP" "$STAGE/"
    ln -s /Applications "$STAGE/Applications"
    cp LEGGIMI.md "$STAGE/LEGGIMI.md"
    DMG="build/Briefing-${VERSION}.dmg"
    rm -f "$DMG"
    hdiutil create -quiet -volname "Briefing ${VERSION}" -srcfolder "$STAGE" -format UDZO "$DMG"
    [[ -n "$IDENTITY" ]] && codesign --force --timestamp --sign "$IDENTITY" "$DMG"
    if [[ -n "${NOTARY_PROFILE:-}" ]]; then
      xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
      xcrun stapler staple "$DMG"
      echo "DMG notarizzato"
    else
      echo "DMG NON notarizzato (manca NOTARY_PROFILE): al primo avvio servirà Impostazioni → Privacy e sicurezza → Apri comunque"
    fi
    echo "Creato $DMG"
    ;;
esac
