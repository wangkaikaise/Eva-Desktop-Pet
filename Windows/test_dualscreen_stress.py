# -*- coding: utf-8 -*-
"""跨显示器崩溃复现压力测试（单屏开发机模拟双屏场景）。

原理：
1. 合成 WM_DPICHANGED(0x02E0) 消息直接发给 EvaWindow 的 HWND，
   强制 Qt 走"跨屏 DPI 变化 → 重建 backing store"的原生路径
   （这正是双屏不同缩放拖动时触发的代码路径）。
2. 高频在虚拟桌面全范围 move() 窗口，模拟拖动跨屏。
3. 同时保持动画重绘（paintEvent 压力）。

存活且无崩溃 = 测试通过。
"""
import ctypes
import ctypes.wintypes as wt
import sys

import os
os.environ.setdefault("EVA_STRESS_TEST", "1")

# 与 main.py 相同的 DPI 上下文，保证测试环境一致
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer, QRect

from settings import SettingsRepository
from eva_window import EvaWindow

user32 = ctypes.windll.user32
WM_DPICHANGED = 0x02E0
WM_DISPLAYCHANGE = 0x007E


def make_wparam(dpi_x: int, dpi_y: int) -> int:
    return (dpi_y & 0xFFFF) << 16 | (dpi_x & 0xFFFF)


class StressDriver:
    def __init__(self, app: QApplication, window: EvaWindow):
        self.app = app
        self.window = window
        self.hwnd = int(window.winId())
        self.move_count = 0
        self.dpi_count = 0
        self._dpi_cycle = [96, 144, 96, 120, 96, 168, 96]
        self._dpi_idx = 0
        self._rect_buf = None  # 保持 RECT 缓冲区存活（lparam 指向它）

        # 高频移动定时器（模拟拖动）
        self.move_timer = QTimer(app)
        self.move_timer.timeout.connect(self._do_move)
        self.move_timer.start(16)

        # 周期性合成 DPI 变化
        self.dpi_timer = QTimer(app)
        self.dpi_timer.timeout.connect(self._send_dpi_changed)
        self.dpi_timer.start(250)

        # 周期性模拟屏幕热插拔通知
        self.disp_timer = QTimer(app)
        self.disp_timer.timeout.connect(self._send_display_change)
        self.disp_timer.start(900)

        # 结束计时
        self.deadline = QTimer(app)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self._finish)
        self.deadline.start(8000)

        # 移动轨迹：扫过整个虚拟桌面（含屏幕边界外一点）
        screens = app.screens()
        virt = QRect(screens[0].geometry())
        for s in screens[1:]:
            virt = virt.united(s.geometry())
        self.virt = virt
        self._t = 0.0

    def _do_move(self):
        """沿虚拟桌面画'8'字扫动，反复穿越屏幕边界。"""
        self._t += 0.06
        import math
        w = self.virt.width() - self.window.width()
        h = self.virt.height() - self.window.height()
        if w <= 0:
            w = 100
        if h <= 0:
            h = 100
        x = self.virt.left() + int((math.sin(self._t) * 0.5 + 0.5) * w)
        y = self.virt.top() + int((math.sin(self._t * 1.7) * 0.5 + 0.5) * h)
        try:
            self.window.move(x, y)
            self.move_count += 1
        except Exception:
            pass

    def _send_dpi_changed(self):
        """合成 WM_DPICHANGED：wParam=新DPI，lparam=建议窗口矩形指针。"""
        dpi = self._dpi_cycle[self._dpi_idx % len(self._dpi_cycle)]
        self._dpi_idx += 1
        old_dpi = 96
        ratio = dpi / old_dpi
        g = self.window.frameGeometry()
        cx, cy = g.center().x(), g.center().y()
        new_w = max(1, int(g.width() * ratio))
        new_h = max(1, int(g.height() * ratio))
        # 建议矩形：按比例缩放并保持中心
        rect = wt.RECT(
            cx - new_w // 2, cy - new_h // 2,
            cx - new_w // 2 + new_w, cy - new_h // 2 + new_h,
        )
        self._rect_buf = rect  # 保活，Windows 会在处理期间读取
        res = user32.SendMessageTimeoutW(
            self.hwnd, WM_DPICHANGED, make_wparam(dpi, dpi),
            ctypes.byref(self._rect_buf), 0x0002, 3000, None)  # SMTO_ABORTIFHUNG
        self.dpi_count += 1
        if self.dpi_count % 4 == 0:
            print(f"  [dpi] 第 {self.dpi_count} 次 DPI 变化(→{dpi}) 已处理, 窗口仍在 "
                  f"dpr={self.window.devicePixelRatioF():.2f}", flush=True)

    def _send_display_change(self):
        """合成 WM_DISPLAYCHANGE（屏幕热插拔广播）。"""
        user32.PostMessageW(self.hwnd, WM_DISPLAYCHANGE, 0, 0)

    def _finish(self):
        print(f"\n=== 存活：压力测试完成 ===", flush=True)
        print(f"move 次数: {self.move_count}, DPI 变化次数: {self.dpi_count}", flush=True)
        print("RESULT: SURVIVED", flush=True)
        self.app.quit()


def main():
    app = QApplication(sys.argv)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    repo = SettingsRepository()
    settings = repo.load_settings()
    window = EvaWindow(settings, repo)
    window.show()
    print(f"窗口 HWND: {int(window.winId())}, 屏幕: "
          f"{[s.name() for s in app.screens()]}", flush=True)
    print("开始 8 秒跨屏压力测试（合成 WM_DPICHANGED + 高频 move）...", flush=True)

    driver = StressDriver(app, window)  # noqa: F841 保活
    ret = app.exec()
    print(f"事件循环退出码: {ret}", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
