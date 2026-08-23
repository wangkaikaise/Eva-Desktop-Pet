# -*- coding: utf-8 -*-
"""查询 Windows 应用程序事件日志中 EvaDesktopPet / python 的崩溃记录"""
import subprocess
import sys

try:
    out = subprocess.run(
        ["wevtutil", "qe", "Application", "/q:*[System[(EventID=1000)]]", "/c:40", "/rd:true", "/f:text"],
        capture_output=True, timeout=30)
    text = out.stdout.decode("gbk", errors="replace")
except Exception as e:
    print("查询失败:", e)
    sys.exit(1)

blocks = text.split("Event[")
hits = []
cur = []
for b in blocks:
    low = b.lower()
    if "evadesktoppet" in low or "python" in low or "librehardware" in low or "pawnio" in low:
        hits.append("Event[" + b[:1800])

if hits:
    print(f"找到 {len(hits)} 条相关崩溃记录：\n")
    print("\n=====\n".join(hits[:5]))
else:
    print("事件日志中无 EvaDesktopPet/python 相关崩溃记录（最近40条 APP Crash 中）")
