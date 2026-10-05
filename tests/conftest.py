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

"""
conftest.py

Дві речі:

1) Додає корінь проєкту в sys.path, щоб тести могли робити
   `import gemini_tools` / `import video_tools` незалежно від того,
   звідки запущено pytest (корінь проєкту чи tests/).

2) Підставляє МІНІМАЛЬНУ заглушку модуля `config`, ЯКЩО справжнього
   config.py немає на PYTHONPATH (наприклад, у CI чи "голому" чекауті
   без реального ключа/налаштувань). gemini_tools.py імпортує `config`
   на рівні модуля (import config), але звертається до нього лише
   всередині функцій (config.load_api_key() - див. gemini_tools.py) -
   тож для тестів, які не викликають реальних HTTP-запитів
   (_analyze_batch_resilient тестується через mock _analyze_batch,
   а не через реальний check_api_key/find_object_in_frames), досить,
   щоб `import config` просто не падав.

Якщо у репозиторії вже є справжній config.py - він і буде знайдений
першим (sys.path.insert ставить корінь проєкту ПЕРЕД заглушкою) і
ніяка заглушка не підміняє його.
"""
import os
import sys
import types

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    import config  # noqa: F401  - є справжній config.py в проєкті, лишаємо як є
except ImportError:
    stub = types.ModuleType("config")
    stub.load_api_key = lambda: "test-api-key-stub"
    stub.save_api_key = lambda key: None
    sys.modules["config"] = stub

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
