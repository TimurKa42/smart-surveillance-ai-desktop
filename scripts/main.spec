# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# LookBack
# © 2026 Тимур Каленик (Timur Kalenik Corporation) <timurkalenik950@gmail.com>
# Автор: Тимур Каленик. Усі права захищено.
#
# Проєкт створено для конкурсу МАН. Вихідний код відкрито для ознайомлення
# та оцінювання журі. Копіювання, поширення, зміна та використання коду
# (повністю або частково) без письмової згоди автора та без зазначення
# авторства заборонені. Повні умови — у файлі LICENSE.
# =============================================================================

# збірка: pyinstaller --noconfirm --clean main.spec
# пайінсталер не вміє крос-компіляцію: ехе збираю на віндовс,
# апп на маку. запускати з теки проєкту, в активованому венв311, щоб не забути
#
# важливо: перед збіркою поруч з мейн.пай має лежати embedded_key.py з
# вшитим ключем (дивись embedded_key.example.py). без нього збірка вийде
# без стандартного ключа і доведеться вводити свій, не забути!
import sys
from PyInstaller.utils.hooks import collect_all

APP_NAME = "LookBack"          # ім'я ехе і теки даних, без пробілів
DISPLAY_NAME = "LookBack"      # ім'я апп в док/файндері
ICON_WIN = "assets/icon.ico"
ICON_MAC = "assets/icon.icns"
VERSION_WIN = "version_info.txt"   # дані про автора у властивостях ехе на віндовс

datas = []
binaries = []
hiddenimports = []

# беру кастомткінтер (теми), ткінтердндту (драг-енд-дроп) і ав (ффмпег длл для швидкої нарізки)
for package in ("customtkinter", "tkinterdnd2", "av"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# вшитий апі-ключ, файл у гітігнор, створюю його перед збіркою.
# конфіг імпортує його через трай/ексепт, тому пишу явно,
# щоб пайінсталер точно його запакував
# video_fast імпортується всередині ф-ції, теж пишу явно, щоб не забути
hiddenimports += ["embedded_key", "video_fast"]

# свої ресурси: іконка вікна (читається через app_paths.resource_path)
datas += [("assets/icon.ico", "assets"), ("assets/icon_1024.png", "assets")]

# пайобджі-сі для нативних сповіщень мак-ос (notify_tools.py)
if sys.platform == "darwin":
    hiddenimports += [
        "objc", "Foundation", "AppKit", "UserNotifications", "PyObjCTools",
    ]

# сповіщення віндовс (notify_tools.py): плаєр вантажить бекенд динамічно,
# пайінсталер його не бачить, тому додаю сам. вінотіфай головний шлях
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
    exclude_binaries=True,      # ондір: бібліотеки лежать поруч, а не в самому ехе
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # упх часто викликає хибні спрацювання антивірусів, не вмикати
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
        bundle_identifier="com.lookback.ai",
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
