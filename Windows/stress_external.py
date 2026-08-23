# -*- coding: utf-8 -*-
"""外部压力驱动：对运行中的 EvaDesktopPet.exe（旧版）发送合成 WM_DPICHANGED
和高频 SetWindowPos 移动，验证旧版是否复现 0xc0000409 崩溃。"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_DPICHANGED = 0x02E0


def find_window(title: str):
    result = ctypes.c_void_p()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, lparam):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == title:
            result.value = hwnd
            return False
        return True

    user32.EnumWindows(cb, 0)
    return result.value


def make_wparam(dpi_x, dpi_y):
    return (dpi_y & 0xFFFF) << 16 | (dpi_x & 0xFFFF)


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else None
    if not exe:
        print("用法: python stress_external.py <exe路径>")
        sys.exit(2)

    proc = subprocess.Popen([exe])
    print(f"已启动 PID={proc.pid}，等待窗口...", flush=True)

    hwnd = None
    for _ in range(60):
        time.sleep(0.5)
        hwnd = find_window("伊娃桌面宠物")
        if hwnd:
            break
    if not hwnd:
        print("未找到窗口")
        proc.kill()
        sys.exit(3)
    print(f"窗口 HWND={hwnd}", flush=True)

    import math
    rect = wt.RECT()
    dpi_cycle = [96, 144, 96, 120, 96, 168, 96]
    dpi_i = 0
    moves = dpis = 0
    start = time.time()
    alive_seconds = 0.0

    # 8 秒压力
    last_alive_check = time.time()
    while time.time() - start < 8.0:
        if proc.poll() is not None:
            print(f"!!! 进程已退出，退出码 {proc.returncode}（复现崩溃）")
            print(f"RESULT: CRASHED (moves={moves}, dpi={dpis}, alive={alive_seconds:.1f}s)")
            sys.exit(1)
        alive_seconds = time.time() - start

        # 高频移动（沿正弦扫动）
        t = time.time() - start
        x = int((math.sin(t * 3) * 0.5 + 0.5) * 1500)
        y = int((math.sin(t * 5.1) * 0.5 + 0.5) * 800)
        user32.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x0001 | 0x0004)  # NOSIZE|NOZORDER
        moves += 1

        # 每 ~200ms 一次 DPI 变化
        if int(t * 5) != int((t - 0.04) * 5):
            dpi = dpi_cycle[dpi_i % len(dpi_cycle)]
            dpi_i += 1
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                g_w = rect.right - rect.left
                g_h = rect.bottom - rect.top
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                ratio = dpi / 96
                nw, nh = int(g_w * ratio), int(g_h * ratio)
                r = wt.RECT(cx - nw // 2, cy - nh // 2, cx - nw // 2 + nw, cy - nh // 2 + nh)
                user32.SendMessageTimeoutW(
                    hwnd, WM_DPICHANGED, make_wparam(dpi, dpi),
                    ctypes.byref(r), 0x0002, 2000, None)
                dpis += 1
        time.sleep(0.02)

    if proc.poll() is not None:
        print(f"RESULT: CRASHED (moves={moves}, dpi={dpis})")
        sys.exit(1)

    print(f"=== 存活 === moves={moves}, dpi={dpis}")
    print("RESULT: SURVIVED")
    proc.terminate()
    try:
        proc.wait(5)
    except Exception:
        proc.kill()
    sys.exit(0)


if __name__ == "__main__":
    main()
