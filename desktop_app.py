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
import uuid

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

# Local, per-machine state (tasks/categories per profile). Lives next to the app,
# NOT tracked by git (see .gitignore) — a `git pull` must never touch this data.
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
PROFILES_INDEX_PATH = os.path.join(PROFILES_DIR, "index.json")
DEFAULT_PROFILE_ID = "default"


def _ensure_profiles_dir():
    os.makedirs(PROFILES_DIR, exist_ok=True)


def _profile_path(profile_id):
    safe = "".join(c for c in profile_id if c.isalnum() or c in "-_") or DEFAULT_PROFILE_ID
    return os.path.join(PROFILES_DIR, f"{safe}.json")


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    _ensure_profiles_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_index():
    _ensure_profiles_dir()
    if not os.path.exists(PROFILES_INDEX_PATH):
        index = {
            "activeProfileId": DEFAULT_PROFILE_ID,
            "profiles": [{"id": DEFAULT_PROFILE_ID, "name": "По умолчанию"}],
        }
        _save_json(PROFILES_INDEX_PATH, index)
        _save_json(_profile_path(DEFAULT_PROFILE_ID),
                   {"name": "По умолчанию", "categories": [], "tasks": []})
        return index
    index = _load_json(PROFILES_INDEX_PATH, None)
    if not index or not index.get("profiles"):
        index = {
            "activeProfileId": DEFAULT_PROFILE_ID,
            "profiles": [{"id": DEFAULT_PROFILE_ID, "name": "По умолчанию"}],
        }
        _save_json(PROFILES_INDEX_PATH, index)
    return index


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
        """Reports whether an update is available, for two different reasons:

        1) "git_behind" — origin on GitHub has commits this local repo doesn't
           (the classic "another machine/copy pushed something new" case).
        2) "stale_build" — when running as a frozen .exe: the version.json at
           the project's source root no longer matches the version that was
           baked into THIS .exe at build time. This catches the case where
           you (or I) just edited files locally and bumped version.json —
           nothing has necessarily been pushed anywhere, but the running .exe
           is still serving the old, already-compiled code and should be
           rebuilt. On a single machine this is usually the one that fires,
           since local == origin the moment you push it yourself.
        """
        git_behind = False
        git_error = None
        try:
            fetch = self._run(["git", "fetch"])
            if fetch.returncode != 0:
                git_error = fetch.stderr.strip() or "git fetch failed"
            else:
                local = self._run(["git", "rev-parse", "HEAD"])
                remote = self._run(["git", "rev-parse", "@{u}"])
                if local.returncode != 0 or remote.returncode != 0:
                    git_error = (remote.stderr or local.stderr or "").strip() or \
                        "Не настроен upstream. Выполните: git push -u origin main"
                else:
                    git_behind = local.stdout.strip() != remote.stdout.strip()
        except FileNotFoundError:
            git_error = "Git не установлен или не найден в PATH."
        except subprocess.TimeoutExpired:
            git_error = "Превышено время ожидания git fetch."
        except Exception as e:
            git_error = str(e)

        stale_build = False
        source_version = None
        baked_version = None
        project_root = None
        if getattr(sys, "frozen", False):
            project_root = self._find_project_root()
            baked_version_data = _load_json(VERSION_PATH, None)
            baked_version = (baked_version_data or {}).get("version")
            if project_root:
                source_version_data = _load_json(
                    os.path.join(project_root, "version.json"), None)
                source_version = (source_version_data or {}).get("version")
                if source_version and baked_version and source_version != baked_version:
                    stale_build = True

        # If git itself failed AND we have no other way to detect an update,
        # surface the git error as before. But a stale local build is still
        # worth reporting even when git fetch failed (e.g. offline) — in that
        # case rebuilding still works, it just won't also `git pull` first
        # (update_and_restart's pull is a safe no-op / harmless failure then).
        if git_error and not stale_build:
            return {"ok": False, "error": git_error}

        return {
            "ok": True,
            "hasUpdate": git_behind or stale_build,
            "gitBehind": git_behind,
            "sourceVersion": source_version,
            "bakedVersion": baked_version,
            "projectRoot": project_root,
            "staleBuild": stale_build,
        }

    # ---------- Profiles (local state) ----------

    def list_profiles(self):
        index = _load_index()
        return {"ok": True, "profiles": index["profiles"], "activeProfileId": index["activeProfileId"]}

    def get_profile(self, profile_id):
        data = _load_json(_profile_path(profile_id), {"name": profile_id, "categories": [], "tasks": []})
        return {"ok": True, "data": data}

    def save_profile(self, profile_id, data):
        try:
            _save_json(_profile_path(profile_id), data)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def create_profile(self, name):
        name = (name or "").strip() or "Без названия"
        index = _load_index()
        new_id = uuid.uuid4().hex[:8]
        index["profiles"].append({"id": new_id, "name": name})
        index["activeProfileId"] = new_id
        _save_json(PROFILES_INDEX_PATH, index)
        _save_json(_profile_path(new_id), {"name": name, "categories": [], "tasks": []})
        return {"ok": True, "id": new_id}

    def rename_profile(self, profile_id, name):
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "Имя не может быть пустым."}
        index = _load_index()
        found = False
        for p in index["profiles"]:
            if p["id"] == profile_id:
                p["name"] = name
                found = True
        if not found:
            return {"ok": False, "error": "Профиль не найден."}
        _save_json(PROFILES_INDEX_PATH, index)
        data = _load_json(_profile_path(profile_id), {"categories": [], "tasks": []})
        data["name"] = name
        _save_json(_profile_path(profile_id), data)
        return {"ok": True}

    def delete_profile(self, profile_id):
        index = _load_index()
        if len(index["profiles"]) <= 1:
            return {"ok": False, "error": "Нельзя удалить последний профиль."}
        index["profiles"] = [p for p in index["profiles"] if p["id"] != profile_id]
        if index["activeProfileId"] == profile_id:
            index["activeProfileId"] = index["profiles"][0]["id"]
        _save_json(PROFILES_INDEX_PATH, index)
        try:
            os.remove(_profile_path(profile_id))
        except FileNotFoundError:
            pass
        return {"ok": True, "activeProfileId": index["activeProfileId"]}

    def set_active_profile(self, profile_id):
        index = _load_index()
        if not any(p["id"] == profile_id for p in index["profiles"]):
            return {"ok": False, "error": "Профиль не найден."}
        index["activeProfileId"] = profile_id
        _save_json(PROFILES_INDEX_PATH, index)
        return {"ok": True}

    def _find_project_root(self):
        """The git repo's top-level folder — computed via git itself, so it
        works regardless of how deep BASE_DIR is nested (e.g. dist/desktop_app/
        for a frozen build)."""
        res = self._run(["git", "rev-parse", "--show-toplevel"])
        if res.returncode != 0:
            return None
        # git always returns forward slashes; normalize for the current OS
        return os.path.normpath(res.stdout.strip())

    def _rebuild_exe(self):
        """Re-runs PyInstaller from the freshly-pulled source so the bundled
        HTML/Python inside the .exe actually reflects the update — a plain
        relaunch of a frozen build would just re-run the OLD baked-in code,
        since PyInstaller bakes files in at build time and `git pull` only
        touches the source folder, never the compiled bundle."""
        project_root = self._find_project_root()
        if not project_root:
            return {"ok": False, "error": "Не удалось определить корень git-репозитория для пересборки."}

        add_data_html = f"weekly_planner.html{os.pathsep}."
        add_data_version = f"version.json{os.pathsep}."
        candidates = [
            ["pyinstaller", "--onedir", "--noconfirm",
             "--add-data", add_data_html, "--add-data", add_data_version, "desktop_app.py"],
            ["python", "-m", "PyInstaller", "--onedir", "--noconfirm",
             "--add-data", add_data_html, "--add-data", add_data_version, "desktop_app.py"],
            ["py", "-m", "PyInstaller", "--onedir", "--noconfirm",
             "--add-data", add_data_html, "--add-data", add_data_version, "desktop_app.py"],
        ]

        last_error = "PyInstaller не найден (ни pyinstaller, ни python -m PyInstaller, ни py -m PyInstaller)."
        for cmd in candidates:
            try:
                result = subprocess.run(
                    cmd, cwd=project_root, capture_output=True, text=True, timeout=240,
                )
            except FileNotFoundError:
                continue
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "Превышено время ожидания пересборки (PyInstaller)."}

            if result.returncode == 0:
                exe_name = "desktop_app.exe" if sys.platform.startswith("win") else "desktop_app"
                exe_path = os.path.join(project_root, "dist", "desktop_app", exe_name)
                if os.path.exists(exe_path):
                    return {"ok": True, "exe_path": exe_path}
                last_error = "Пересборка завершилась без ошибок, но новый .exe не найден по ожидаемому пути."
            else:
                last_error = ((result.stderr or result.stdout or "").strip())[-2000:] or "PyInstaller вернул ошибку."
            break  # this candidate command WAS found and ran — don't silently try alternates on a real failure

        return {"ok": False, "error": "Не удалось пересобрать .exe: " + last_error}

    def update_and_restart(self):
        """Pulls latest changes (if any), rebuilds the .exe if running as one
        (so the update actually takes effect), and relaunches.

        A `git pull` failure here (e.g. no network, or nothing to pull from
        because the "update" is really just a locally-edited, not-yet-pushed
        stale build) is not fatal on its own — we still try to rebuild from
        whatever source is on disk right now, since that's what the user
        actually asked for. We only give up if BOTH pulling and rebuilding
        are impossible."""
        pull_error = None
        try:
            pull = self._run(["git", "pull", "--ff-only"], timeout=40)
            if pull.returncode != 0:
                pull_error = pull.stderr.strip() or pull.stdout.strip()
        except Exception as e:
            pull_error = str(e)

        if getattr(sys, "frozen", False):
            rebuild = self._rebuild_exe()
            if not rebuild["ok"]:
                # Rebuild is the only thing that matters for a frozen build —
                # if it worked, a failed/no-op pull above doesn't matter.
                if pull_error:
                    rebuild["error"] = rebuild["error"] + f" (также не удалось выполнить git pull: {pull_error})"
                return rebuild
            new_exe = rebuild["exe_path"]
            os.execv(new_exe, [new_exe])
            return {"ok": True}  # unreachable, kept for clarity

        # Not frozen: there's nothing to "rebuild" — the script IS the source,
        # so if git pull didn't succeed, relaunching would just rerun the
        # exact same code and silently look like nothing happened.
        if pull_error:
            return {"ok": False, "error": pull_error}

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