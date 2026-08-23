# -*- coding: utf-8 -*-
"""冒烟测试：完整启动 EvaWindow → 运行 6 秒 → 走 _quit 退出路径，验证干净退出无崩溃。"""
import sys

try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer

from settings import SettingsRepository
from eva_window import EvaWindow


def main():
    app = QApplication(sys.argv)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    repo = SettingsRepository()
    settings = repo.load_settings()
    window = EvaWindow(settings, repo)
    window.show()
    print("启动完成，6 秒后模拟托盘退出...", flush=True)

    def do_quit():
        print("触发 _quit()...", flush=True)
        window._quit()

    QTimer.singleShot(6000, do_quit)
    ret = app.exec()
    print(f"事件循环退出码: {ret}", flush=True)
    print("RESULT: CLEAN_EXIT", flush=True)
    sys.exit(0 if ret == 0 else 1)


if __name__ == "__main__":
    main()
