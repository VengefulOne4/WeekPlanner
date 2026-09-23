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
import shutil
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


def _detect_git_project_root(base_dir):
    """Best-effort git-toplevel lookup, usable at module load time (before
    the Api class — and its subprocess helper — exist)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=base_dir, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return os.path.normpath(result.stdout.strip())
    except Exception:
        pass
    return None


def get_data_dir():
    """Stable folder for the user's own local data (profiles/) — must NOT be
    the same folder PyInstaller rebuilds into.

    For a frozen .exe, BASE_DIR is dist/desktop_app/ — exactly the folder
    that gets wiped clean on every rebuild (both the old in-process rebuild
    and the current updater.py both do this; it's simply how PyInstaller
    always builds --onedir output). If profiles/ lived inside BASE_DIR, an
    update would silently delete the user's saved schedules along with the
    old .exe. So for a frozen build we instead resolve the stable git
    project root and keep profiles/ there, untouched by any rebuild — and
    only fall back to BASE_DIR if that can't be determined (e.g. this isn't
    a git repo at all, in which case auto-rebuild can't run either way, so
    there's nothing at risk from keeping the old behavior)."""
    if getattr(sys, "frozen", False):
        root = _detect_git_project_root(BASE_DIR)
        if root:
            return root
    return BASE_DIR


DATA_DIR = get_data_dir()
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
PROFILES_INDEX_PATH = os.path.join(PROFILES_DIR, "index.json")
DEFAULT_PROFILE_ID = "default"


def _migrate_profiles_out_of_build_dir():
    """One-time move for anyone who already has profiles/ sitting inside the
    old, unsafe location (BASE_DIR — the rebuilt-on-every-update folder).
    Runs once at startup; harmless no-op afterwards."""
    if DATA_DIR == BASE_DIR:
        return
    old_dir = os.path.join(BASE_DIR, "profiles")
    if os.path.isdir(old_dir) and not os.path.isdir(PROFILES_DIR):
        try:
            os.makedirs(os.path.dirname(PROFILES_DIR), exist_ok=True)
            shutil.move(old_dir, PROFILES_DIR)
        except Exception:
            pass  # best-effort — worst case the user keeps their old profiles/ in place


_migrate_profiles_out_of_build_dir()


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
                # A missing/unreadable baked version.json (baked_version is None)
                # counts as stale too, not "nothing to compare" — it usually means
                # this particular .exe was built without version.json bundled
                # (e.g. from an older build command, or a dist/ folder that
                # predates this check), so it can't possibly be up to date.
                if source_version and source_version != baked_version:
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

    def update_and_restart(self):
        """Applies the update and relaunches.

        Not frozen (`python desktop_app.py`): the script IS the source, so a
        plain `git pull` + relaunching the interpreter is enough — nothing
        needs rebuilding.

        Frozen (.exe): rebuilding the .exe in place is impossible while it's
        running — Windows keeps a running executable's file locked against
        delete/replace, and PyInstaller starts every build by wiping its
        output folder first. So instead we hand off to updater.py, a plain
        script (not bundled into the .exe) that runs as its OWN independent
        process: we spawn it detached, then this process exits immediately
        to release the lock on its own .exe file. updater.py then waits for
        us to actually be gone, does `git pull` + rebuilds the .exe (now
        free to overwrite) with PyInstaller, and launches the fresh build.
        This keeps a single dist/ folder instead of needing two."""
        if not getattr(sys, "frozen", False):
            try:
                pull = self._run(["git", "pull", "--ff-only"], timeout=40)
                if pull.returncode != 0:
                    return {"ok": False, "error": pull.stderr.strip() or pull.stdout.strip()}
            except Exception as e:
                return {"ok": False, "error": str(e)}
            python = sys.executable
            os.execv(python, [python] + sys.argv)
            return {"ok": True}  # unreachable, kept for clarity

        project_root = self._find_project_root()
        if not project_root:
            return {"ok": False, "error": "Не удалось определить корень git-репозитория для обновления."}

        updater_script = os.path.join(project_root, "updater.py")
        if not os.path.exists(updater_script):
            return {
                "ok": False,
                "error": "Не найден updater.py в корне проекта. Скачайте его туда же, где лежат "
                         "desktop_app.py и weekly_planner.html, и попробуйте снова.",
            }

        popen_kwargs = {}
        if sys.platform.startswith("win"):
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            popen_kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        args_tail = ["--pid", str(os.getpid()), "--project-root", project_root]
        candidates = [
            ["python", updater_script],
            ["py", updater_script],
            ["python3", updater_script],
        ]
        spawned = False
        for cmd in candidates:
            try:
                subprocess.Popen(cmd + args_tail, cwd=project_root, **popen_kwargs)
                spawned = True
                break
            except FileNotFoundError:
                continue
        if not spawned:
            return {"ok": False, "error": "Не найден Python в PATH — не удалось запустить процесс обновления."}

        # The updater process is now running independently of us and will
        # outlive this process. Exit immediately (not a graceful shutdown —
        # we don't need one) so our own .exe file is no longer locked and
        # the updater can rebuild it.
        os._exit(0)
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
    print(f"PROFILES_DIR (ваши задачи/категории): {PROFILES_DIR}")

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