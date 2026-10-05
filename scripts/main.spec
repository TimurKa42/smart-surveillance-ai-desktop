# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# Smart Surveillance AI
# © 2026 Тимур Каленик (Timur Kalenik Corporation) <timurkalenik950@gmail.com>
# Автор: Тимур Каленик. Усі права захищено.
#
# Проєкт створено для конкурсу МАН. Вихідний код відкрито для ознайомлення
# та оцінювання журі. Копіювання, поширення, зміна та використання коду
# (повністю або частково) без письмової згоди автора та без зазначення
# авторства заборонені. Повні умови — у файлі LICENSE.
# =============================================================================

#
# Збірка:   pyinstaller --noconfirm --clean main.spec
#
# PyInstaller не вміє крос-компіляцію: .exe збирається на Windows,
# .app - на macOS. Запускати з теки проєкту, у активованому .venv311.
import sys
from PyInstaller.utils.hooks import collect_all

APP_NAME = "SmartSurveillanceAI"          # имя exe и папки данных - без пробелов
DISPLAY_NAME = "Smart Surveillance AI"    # имя .app в Dock/Finder - с пробелами
ICON_WIN = "assets/icon.ico"
ICON_MAC = "assets/icon.icns"
VERSION_WIN = "version_info.txt"   # метадані автора в властивостях .exe (Windows)

datas = []
binaries = []
hiddenimports = []

for package in ("customtkinter",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Власні ресурси: іконка вікна (читається через app_paths.resource_path)
datas += [("assets/icon.ico", "assets"), ("assets/icon_1024.png", "assets")]

# pyobjc для нативних сповіщень macOS (notify_tools.py)
if sys.platform == "darwin":
    hiddenimports += [
        "objc", "Foundation", "AppKit", "UserNotifications", "PyObjCTools",
    ]

# Сповіщення Windows (notify_tools.py): plyer вантажить бекенд динамічно,
# тож PyInstaller його не бачить - додаємо явно. winotify - основний шлях.
if sys.platform == "win32":
    hiddenimports += [
        "plyer.platforms.win.notification",
        "winotify",
    ]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # onedir: бібліотеки лежать поруч, а не в самому exe
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # UPX часто викликає хибні спрацювання антивірусів
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_WIN if sys.platform == "win32" else None,
    version=VERSION_WIN if sys.platform == "win32" else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{DISPLAY_NAME}.app",
        icon=ICON_MAC,
        bundle_identifier="com.smartsurveillance.ai",
        info_plist={
            "CFBundleName": DISPLAY_NAME,
            "CFBundleDisplayName": DISPLAY_NAME,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "© 2026 Тимур Каленик (Timur Kalenik Corporation). Усі права захищено.",
            "NSCameraUsageDescription": "Доступ до камери потрібен для аналізу відеопотоку.",
            "NSMicrophoneUsageDescription": "Доступ до мікрофона потрібен для запису відео зі звуком.",
        },
    )

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
