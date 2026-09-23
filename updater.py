"""
Обновляющий процесс — намеренно ОТДЕЛЬНЫЙ от самого приложения.

Этот файл НЕ запекается внутрь desktop_app.exe и не пытается обновить сам
себя. Он запускается как независимый процесс прямо перед тем, как
desktop_app.exe закрывается для обновления: пока .exe работает, Windows
держит его файл заблокированным от удаления/перезаписи, а PyInstaller при
сборке сначала удаляет всю папку назначения — так что пересобрать .exe,
пока он же и работает, невозможно в принципе. Раз этот скрипт — не .exe,
а обычный python-процесс, он не заблокирован и может пересобрать всё,
что нужно, уже после того, как старый .exe завершится.

Запускается автоматически кнопкой "Обновить" в приложении. Вручную
запускать не нужно, но можно — например, если само приложение уже не
запускается и нужно обновить его "снаружи":

    python updater.py --pid 0 --project-root "F:\\Projects\\Weekly Planner"

(--pid 0 означает "не жди никакой процесс, сразу пересобирай")
"""

import argparse
import os
import subprocess
import sys
import time


def log_path(project_root):
    return os.path.join(project_root, "updater.log")


def log(project_root, message):
    try:
        with open(log_path(project_root), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def is_pid_running(pid):
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            out = subprocess.run(
                ["tasklist", "/fi", f"PID eq {pid}"],
                capture_output=True, text=True, timeout=10,
            )
            return str(pid) in out.stdout
        except Exception:
            # If we can't even check, assume it's gone rather than hang forever.
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
        except Exception:
            return False


def wait_for_exit(pid, timeout=30):
    if pid <= 0:
        return True
    start = time.time()
    while time.time() - start < timeout:
        if not is_pid_running(pid):
            return True
        time.sleep(0.3)
    return not is_pid_running(pid)


def rebuild(project_root):
    """Runs PyInstaller from project_root into the single dist/ folder —
    safe now, since the old .exe (if any) is confirmed gone by this point."""
    add_data_html = f"weekly_planner.html{os.pathsep}."
    add_data_version = f"version.json{os.pathsep}."
    distpath = os.path.join(project_root, "dist")
    workpath = os.path.join(project_root, "build")
    common_args = [
        "--onedir", "--noconfirm",
        "--distpath", distpath, "--workpath", workpath,
        "--add-data", add_data_html, "--add-data", add_data_version, "desktop_app.py",
    ]
    candidates = [
        ["pyinstaller"] + common_args,
        ["python", "-m", "PyInstaller"] + common_args,
        ["py", "-m", "PyInstaller"] + common_args,
    ]

    for cmd in candidates:
        try:
            result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=240)
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            log(project_root, "Пересборка превысила время ожидания (240с).")
            return None

        if result.returncode == 0:
            exe_name = "desktop_app.exe" if sys.platform.startswith("win") else "desktop_app"
            exe_path = os.path.join(distpath, "desktop_app", exe_name)
            if os.path.exists(exe_path):
                return exe_path
            log(project_root, "Сборка завершилась без ошибок, но .exe не найден: " + exe_path)
            return None
        else:
            log(project_root, "PyInstaller вернул ошибку:\n" + (result.stderr or result.stdout or ""))
            return None

    log(project_root, "PyInstaller не найден (pyinstaller / python -m PyInstaller / py -m PyInstaller).")
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, default=0,
                         help="PID процесса приложения, который нужно дождаться перед обновлением.")
    parser.add_argument("--project-root", required=True,
                         help="Корень git-репозитория (папка с desktop_app.py).")
    args = parser.parse_args()

    project_root = os.path.normpath(args.project_root)
    log(project_root, f"--- updater запущен (ждём завершения pid={args.pid}) ---")

    if not wait_for_exit(args.pid, timeout=30):
        log(project_root, "Старое приложение не закрылось за 30 секунд — обновление отменено.")
        return

    try:
        pull = subprocess.run(
            ["git", "pull", "--ff-only"], cwd=project_root,
            capture_output=True, text=True, timeout=40,
        )
        log(project_root, "git pull: " + ((pull.stdout or "") + (pull.stderr or "")).strip())
    except Exception as e:
        # Not fatal — we might just be picking up a purely local edit that
        # was never pushed anywhere; rebuilding from what's on disk still
        # makes sense.
        log(project_root, f"git pull не удался (не критично): {e}")

    exe_path = rebuild(project_root)
    if not exe_path:
        log(project_root, "Обновление не удалось — старое приложение придётся запустить вручную.")
        return

    log(project_root, "Пересобрано успешно, запускаю: " + exe_path)
    try:
        subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path))
    except Exception as e:
        log(project_root, f"Не удалось запустить новую сборку: {e}")


if __name__ == "__main__":
    main()