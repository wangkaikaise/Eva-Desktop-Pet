"""验证 WinDpiNativeFilter 是否真正拦截 WM_DPICHANGED 消息。
在单显示器上通过 PostMessage 合成 DPI 变化消息，确认回调被触发。"""
import sys
import ctypes
import ctypes.wintypes as wt
import time

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

# 先创建 app
app = QApplication(sys.argv)

from eva_window import WinDpiNativeFilter, _extract_msg_id

# 计数器
callback_count = 0

def on_change():
    global callback_count
    callback_count += 1
    print(f"  [callback] WM_DPICHANGED/WM_DISPLAYCHANGE 拦截 #{callback_count}")

# 安装过滤器
flt = WinDpiNativeFilter(on_change)
app.installNativeEventFilter(flt)
print("原生事件过滤器已安装")

# 创建一个透明窗口（和真实场景一致）
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter

class TestWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(200, 200)
        self.move(100, 100)
        self.show()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)

w = TestWidget()
app.processEvents()
time.sleep(0.1)
app.processEvents()

hwnd = int(w.winId())
print(f"窗口 HWND: {hwnd}")

# PostMessage 合成 WM_DPICHANGED
# wParam: HIWORD = Y DPI, LOWORD = X DPI
# lParam: POINTER to RECT (suggested window rect)
WM_DPICHANGED = 0x02E0
WM_DISPLAYCHANGE = 0x011D

# 合成 144 DPI (96 * 1.5)
wparam = (144 << 16) | 96  # HIWORD=144, LOWORD=96
rect = wt.RECT(100, 100, 300, 300)
lparam = ctypes.cast(ctypes.pointer(rect), ctypes.c_void_p).value

print(f"\n发送 WM_DPICHANGED (wParam={wparam:#x})...")
ctypes.windll.user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_void_p]
ctypes.windll.user32.SendMessageW.restype = ctypes.c_ssize_t
result = ctypes.windll.user32.SendMessageW(hwnd, WM_DPICHANGED, wparam, lparam)
app.processEvents()
time.sleep(0.05)
app.processEvents()
print(f"  回调触发次数: {callback_count}")

print(f"\n发送 WM_DISPLAYCHANGE...")
result = ctypes.windll.user32.SendMessageW(hwnd, WM_DISPLAYCHANGE, 0, 0)
app.processEvents()
time.sleep(0.05)
app.processEvents()
print(f"  回调触发次数: {callback_count}")

# 验证 _extract_msg_id 能正确解析
print("\n--- 验证 _extract_msg_id ---")
# 构造一个假 MSG bytes: hwnd(8) + message(4) + ...
import struct
fake_msg_bytes = struct.pack("<QIIQQ", 0, WM_DPICHANGED, 0, 0, 0)  # hwnd=0, message=WM_DPICHANGED, wParam=0, lParam=0, time=0
msg_id = _extract_msg_id(fake_msg_bytes)
print(f"  bytes 方式: msg_id={msg_id:#x} (期望 {WM_DPICHANGED:#x}) {'OK' if msg_id == WM_DPICHANGED else 'FAIL'}")

# 构造一个 MSG 结构体在内存中
fake_msg = wt.MSG()
fake_msg.message = WM_DPICHANGED
msg_id = _extract_msg_id(ctypes.addressof(fake_msg))
print(f"  int 方式: msg_id={msg_id:#x} (期望 {WM_DPICHANGED:#x}) {'OK' if msg_id == WM_DPICHANGED else 'FAIL'}")

if callback_count >= 2:
    print("\n=== PASS: 原生过滤器成功拦截 Windows 消息 ===")
else:
    print(f"\n=== WARN: 回调只触发了 {callback_count} 次（可能 PySide6 版本的 message 格式不同）===")
    # 尝试诊断 message 的实际类型
    print("  尝试诊断 PySide6 nativeEventFilter 的 message 参数类型...")
    diag_count = 0
    class DiagFilter:
        pass
    from PySide6.QtCore import QAbstractNativeEventFilter
    class DiagFilter2(QAbstractNativeEventFilter):
        def nativeEventFilter(self, eventType, message):
            global diag_count
            if eventType == b"windows_generic_MSG":
                diag_count += 1
                if diag_count <= 5:
                    print(f"    msg type={type(message).__name__}, value={repr(message)[:100]}")
            return False
    diag = DiagFilter2()
    app.installNativeEventFilter(diag)
    ctypes.windll.user32.SendMessageW(hwnd, WM_DPICHANGED, wparam, lparam)
    app.processEvents()
    print(f"  诊断过滤器捕获了 {diag_count} 条消息")

QTimer.singleShot(100, app.quit)
app.exec()
print("\n测试完成")
