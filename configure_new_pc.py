"""Interactive setup of a new PC's Groq key and Coursera display name."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def validate_name(name):
    name = name.strip()
    if not name or len(name) > 160 or any(ord(char) < 32 for char in name):
        raise ValueError("Введите имя и фамилию как в Coursera, одной строкой (до 160 символов).")
    return name


def load_notice(path=None):
    path = Path(path) if path is not None else ROOT / "settings.json"
    if not path.exists():
        return None
    settings = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(settings, dict) or not isinstance(settings.get("learner_name"), str):
        raise ValueError("Неверный settings.json. Запустите configure_groq.cmd ещё раз.")
    return "I, " + validate_name(settings["learner_name"]) + ", understand and agree."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-api-check", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise RuntimeError("Эта настройка предназначена для Windows.")
    from groq_credentials import save_key
    from course_state import atomic_json

    print("Настройка Groq на этом компьютере. Ключ вводится скрыто; вставьте его и нажмите Enter.")
    while True:
        key = getpass.getpass("Новый Groq API key (gsk_...): ").strip()
        if key.startswith("gsk_") and len(key) > 12 and not any(char.isspace() for char in key):
            break
        print("Ключ должен начинаться с gsk_ и не содержать пробелов. Получите его на console.groq.com/keys.")
    save_key(key)
    del key
    print("Ключ сохранён для текущего пользователя Windows.")
    while True:
        try:
            name = validate_name(input("Имя и фамилия в аккаунте Coursera (латиницей, как на сайте): "))
            break
        except ValueError as error:
            print(error)
    atomic_json(ROOT / "settings.json", {"learner_name":name})
    print("Имя сохранено. Курсы можно изменить в courses.txt.")
    if not args.skip_api_check:
        print("Проверка Groq коротким пробным запросом...")
        environment = dict(os.environ)
        environment.pop("GROQ_API_KEY", None)
        result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "coursera_start.py"), "--check-api"],
                                cwd=ROOT, env=environment)
        if result.returncode:
            print("Проверка API не прошла. Настройки сохранены; проверьте доступ к модели/лимиты и повторите configure_groq.cmd.")
            return result.returncode
    print("Готово. Запустите run_course.cmd, войдите в отдельном окне Chrome и нажмите Enter в терминале.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nНастройка остановлена. Для продолжения откройте configure_groq.cmd.")
        raise SystemExit(130)
    except Exception as error:
        print("SETUP_ERROR:", str(error) or type(error).__name__)
        raise SystemExit(2)
