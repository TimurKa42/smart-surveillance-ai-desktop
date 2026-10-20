#!/usr/bin/env bash
# збираю дмг з вікном "перетягни в аплікейшнс"
# запускати з теки проєкту: баш build_dmg.sh *щоб не забути*
set -euo pipefail

APP_NAME="LookBack"
APP_PATH="dist/${APP_NAME}.app"
ARCH="$(uname -m)"   # х86_64 або арм64
DMG_PATH="dist/LookBack-${ARCH}.dmg"
STAGING="dist/dmg"

# без .app збирати нема з чого, спочатку пайінсталер
if [ ! -d "$APP_PATH" ]; then
  echo "Не знайдено $APP_PATH — спочатку: pyinstaller --noconfirm --clean main.spec"
  exit 1
fi

# якщо нема креейт-дмг, ставлю через брю
command -v create-dmg >/dev/null 2>&1 || brew install create-dmg

# чищу старе, кидаю .app в тимчасову теку
rm -rf "$STAGING" "$DMG_PATH"
mkdir -p "$STAGING"
ditto "$APP_PATH" "$STAGING/${APP_NAME}.app"

# налаштування вікна дмг (позиції, розмір іконок)
ARGS=(
  --volname "$APP_NAME"
  --window-pos 200 120
  --window-size 600 400
  --icon-size 110
  --icon "${APP_NAME}.app" 150 190
  --hide-extension "${APP_NAME}.app"
  --app-drop-link 450 190
)
# фон беру тільки якщо файл є
[ -f "assets/dmg_background.png" ] && ARGS+=(--background "assets/dmg_background.png")

create-dmg "${ARGS[@]}" "$DMG_PATH" "$STAGING/"

# прибираю за собою
rm -rf "$STAGING"
echo "Готово: $DMG_PATH"
