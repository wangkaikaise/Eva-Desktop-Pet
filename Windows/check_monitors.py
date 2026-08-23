# -*- coding: utf-8 -*-
"""枚举显示器与缩放比（无 GUI，走 Win32 API）"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32
shcore = ctypes.windll.shcore


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
        ("szDevice", wt.WCHAR * 32),
    ]


monitors = []
EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)


def _cb(hmon, hdc, lprect, lparam):
    try:
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            dpi_x = ctypes.c_uint(96)
            dpi_y = ctypes.c_uint(96)
            try:
                shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
            except Exception:
                pass
            r = mi.rcMonitor
            monitors.append({
                "name": mi.szDevice,
                "rect": (r.left, r.top, r.right, r.bottom),
                "work": (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom),
                "primary": bool(mi.dwFlags & 1),
                "scale_pct": round(dpi_x.value / 96 * 100),
            })
    except Exception as e:
        print("callback err:", e)
    return True


user32.EnumDisplayMonitors(None, None, EnumProc(_cb), 0)
print(f"共 {len(monitors)} 个显示器:")
for m in monitors:
    print(f"  {'[主屏]' if m['primary'] else '[副屏]'} {m['name']} "
          f"全区域={m['rect']} 工作区={m['work']} 缩放={m['scale_pct']}%")
