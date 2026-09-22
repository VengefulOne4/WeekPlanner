"""
Недельное расписание — десктопная версия.

Открывает weekly_planner.html в нативном окне (без браузерных панелей)
через pywebview. Весь функционал (категории, минутная точность,
события через полночь) остаётся тем же — это тот же HTML/JS-файл,
просто запущенный как отдельное приложение.

Установка (один раз):
    pip install pywebview --break-system-packages
    (на Linux может понадобиться системный пакет с WebKit — см. README.txt)

Запуск:
    python desktop_app.py
"""

import os
import sys
import webview


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(base_dir, "weekly_planner.html")

    if not os.path.exists(html_path):
        print(f"Не найден файл {html_path}. Убедитесь, что weekly_planner.html "
              f"лежит рядом с desktop_app.py.")
        sys.exit(1)

    webview.create_window(
        "Недельное расписание",
        html_path,
        width=1150,
        height=780,
        min_size=(820, 560),
    )
    webview.start()


if __name__ == "__main__":
    main()
