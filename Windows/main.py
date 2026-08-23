import sys
import os
import ctypes
import logging
import traceback
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from eva_window import EvaWindow
from settings import SettingsRepository
from version import APP_VERSION

APP_NAME = "EvaDesktopPet"


def _setup_logging():
    """商用排障必备：运行期日志落到 %LOCALAPPDATA%/EvaDesktopPet/eva.log。"""
    base_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
    os.makedirs(base_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(base_dir, "eva.log"), maxBytes=512 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # Qt 内部警告/错误也进日志（PySide6 未捕获异常会走这里）
    try:
        from PySide6.QtCore import qInstallMessageHandler

        def _qt_msg(mode, context, message):
            level = {0: logging.DEBUG, 1: logging.WARNING, 2: logging.CRITICAL, 3: logging.CRITICAL}.get(
                int(mode), logging.INFO)
            logging.log(level, "Qt: %s", message)
        qInstallMessageHandler(_qt_msg)
    except Exception:
        pass


def _install_excepthook():
    def _hook(exc_type, exc_value, exc_tb):
        logging.critical("未捕获异常:\n%s", "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = _hook


def _install_faulthandler():
    """原生层崩溃（如访问违例/栈溢出）时，把所有线程的 Python 栈
    落到 crash.log——否则 0xc0000409 这类硬崩在 eva.log 里毫无痕迹。"""
    try:
        import faulthandler
        base_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
        os.makedirs(base_dir, exist_ok=True)
        # 常驻句柄：进程生命周期内有效，避免文件被 GC 关闭
        globals()["_fault_fh"] = open(
            os.path.join(base_dir, "crash.log"), "a", encoding="utf-8")
        faulthandler.enable(file=globals()["_fault_fh"], all_threads=True)
    except Exception:
        pass


# 设置 Per-Monitor DPI Awareness
if os.name == "nt":
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4。旧 API 的参数 2
        # 仅是 Per-Monitor V1，跨屏缩放时更容易出现尺寸跳变。
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

def main():
    _setup_logging()
    _install_excepthook()
    _install_faulthandler()
    log_path = os.path.join(os.environ.get("TEMP", "."), "eva_desktop_pet.log")
    try:
        # 单实例检测（使用互斥体）
        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            mutex = kernel32.CreateMutexW(None, 1, "EvaDesktopPetSingleInstance")
            if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                # 二次启动给出反馈，而不是静默退出
                app = QApplication(sys.argv)
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(None, "伊娃桌面宠物", "伊娃已经在运行了，请查看任务栏托盘。")
                return
            # 防止 mutex 句柄被 GC（保活引用）
            globals()["_singleton_mutex"] = mutex

        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)

        repo = SettingsRepository()
        settings = repo.load_settings()
        logging.info(
            "伊娃 %s 启动，设置: size=%s metrics=%s",
            APP_VERSION, settings.size, settings.metricsEnabled,
        )

        window = EvaWindow(settings, repo)
        window.show()

        sys.exit(app.exec())
    except Exception:
        logging.critical("启动失败:\n%s", traceback.format_exc())
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
