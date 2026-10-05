#!/usr/bin/env bash
# Збирає красивий DMG з вікном «перетягни у Applications».
# Запуск з теки проєкту:  bash build_dmg.sh
set -euo pipefail

APP_NAME="Smart Surveillance AI"
APP_PATH="dist/${APP_NAME}.app"
ARCH="$(uname -m)"   # x86_64 або arm64
DMG_PATH="dist/SmartSurveillanceAI-${ARCH}.dmg"
STAGING="dist/dmg"

if [ ! -d "$APP_PATH" ]; then
  echo "Не знайдено $APP_PATH — спочатку: pyinstaller --noconfirm --clean main.spec"
  exit 1
fi

command -v create-dmg >/dev/null 2>&1 || brew install create-dmg

rm -rf "$STAGING" "$DMG_PATH"
mkdir -p "$STAGING"
ditto "$APP_PATH" "$STAGING/${APP_NAME}.app"

ARGS=(
  --volname "$APP_NAME"
  --window-pos 200 120
  --window-size 600 400
  --icon-size 110
  --icon "${APP_NAME}.app" 150 190
  --hide-extension "${APP_NAME}.app"
  --app-drop-link 450 190
)
[ -f "assets/dmg_background.png" ] && ARGS+=(--background "assets/dmg_background.png")

create-dmg "${ARGS[@]}" "$DMG_PATH" "$STAGING/"

rm -rf "$STAGING"
echo "Готово: $DMG_PATH"