"""
Недельное расписание — десктопная версия.

Открывает weekly_planner.html в нативном окне (без браузерных панелей)
через pywebview. Даёт кнопку "Проверить обновления" в интерфейсе,
которая делает `git fetch` / `git pull` в этой же папке и перезапускает
приложение, если в репозитории появились новые коммиты.

Установка (один раз):
    pip install pywebview --break-system-packages

Запуск:
    python desktop_app.py

Чтобы кнопка обновления заработала, эта папка должна быть git-репозиторием
с настроенным remote "origin" и upstream-веткой (git push -u origin main).
Инструкции — в README.txt.
"""

import json
import os
import subprocess
import sys

import webview


def get_base_dir():
    """The real, persistent folder the app lives in — used for git operations.

    When run as `python desktop_app.py`, this is just the script's own folder.
    When packaged with PyInstaller, sys.executable points at the actual .exe
    on disk (its containing folder, e.g. dist/desktop_app/), which is what we
    want git commands to run in. This is deliberately NOT the same as a
    one-file build's temp extraction folder (sys._MEIPASS) — that folder is
    thrown away and recreated on every launch, so it can never be a git repo.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_dir():
    """Folder to load bundled files (weekly_planner.html, version.json) from.

    PyInstaller extracts --add-data files into sys._MEIPASS at runtime for
    both --onefile and --onedir builds; when not frozen, that's just the
    script's own folder.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return get_base_dir()


BASE_DIR = get_base_dir()          # where git fetch/pull run — must be (in) the repo
RESOURCE_DIR = get_resource_dir()  # where the bundled html/version.json actually are
HTML_PATH = os.path.join(RESOURCE_DIR, "weekly_planner.html")
VERSION_PATH = os.path.join(RESOURCE_DIR, "version.json")


class Api:
    """Exposed to the page as window.pywebview.api.<method>()"""

    def get_version(self):
        try:
            with open(VERSION_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("version", "unknown")
        except Exception:
            return "unknown"

    def _run(self, args, timeout=25):
        return subprocess.run(
            args,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def check_for_update(self):
        """Fetches from origin and reports whether the local branch is behind."""
        try:
            fetch = self._run(["git", "fetch"])
            if fetch.returncode != 0:
                return {"ok": False, "error": fetch.stderr.strip() or "git fetch failed"}

            local = self._run(["git", "rev-parse", "HEAD"])
            remote = self._run(["git", "rev-parse", "@{u}"])
            if local.returncode != 0 or remote.returncode != 0:
                msg = (remote.stderr or local.stderr or "").strip()
                return {
                    "ok": False,
                    "error": msg or "Не настроен upstream. Выполните: git push -u origin main",
                }

            has_update = local.stdout.strip() != remote.stdout.strip()
            return {"ok": True, "hasUpdate": has_update}
        except FileNotFoundError:
            return {"ok": False, "error": "Git не установлен или не найден в PATH."}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Превышено время ожидания git fetch."}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def update_and_restart(self):
        """Pulls latest changes and relaunches the whole process."""
        try:
            pull = self._run(["git", "pull", "--ff-only"], timeout=40)
            if pull.returncode != 0:
                return {"ok": False, "error": pull.stderr.strip() or pull.stdout.strip()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

        # Relaunch the whole process so both the HTML/JS and any Python
        # changes brought in by the update take effect.
        python = sys.executable
        os.execv(python, [python] + sys.argv)
        return {"ok": True}  # unreachable, kept for clarity


def main():
    if not os.path.exists(HTML_PATH):
        print(f"Не найден файл {HTML_PATH}. Убедитесь, что weekly_planner.html "
              f"лежит рядом с desktop_app.py.")
        sys.exit(1)

    try:
        print(f"pywebview {webview.__version__}, платформа: {sys.platform}")
    except Exception:
        pass
    print(f"BASE_DIR (git): {BASE_DIR}")
    print(f"RESOURCE_DIR (html/version): {RESOURCE_DIR}")

    api = Api()
    webview.create_window(
        "Недельное расписание",
        HTML_PATH,
        width=1150,
        height=780,
        min_size=(820, 560),
        js_api=api,
    )
    # debug=True enables right-click -> "Inspect" in the window, so you can open
    # the devtools console and check window.pywebview / any JS errors directly.
    webview.start(debug=True)


if __name__ == "__main__":
    main()