# -*- coding: utf-8 -*-
"""在 WER ReportArchive/ReportQueue 中查找 EvaDesktopPet / python 崩溃报告详情"""
import os
import re

base = os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "Windows", "WER")
hits = []
for sub in ("ReportArchive", "ReportQueue"):
    d = os.path.join(base, sub)
    if not os.path.isdir(d):
        continue
    for entry in os.listdir(d):
        full = os.path.join(d, entry)
        if not os.path.isdir(full):
            continue
        # 读取 Report.wer
        wer = os.path.join(full, "Report.wer")
        if os.path.exists(wer):
            try:
                with open(wer, "r", errors="replace") as f:
                    txt = f.read()
            except Exception:
                continue
            if "EvaDesktopPet" in txt or ("python" in txt.lower() and "ucrtbase" in txt.lower()):
                hits.append((entry, txt))

print(f"找到 {len(hits)} 份相关 WER 报告")
for entry, txt in hits[:3]:
    print("=" * 60)
    print("报告:", entry)
    # 抽取关键字段
    keys = ["EventTime", "Sig[0].Name", "Sig[0].Value", "Sig[1].Value", "Sig[2].Value",
            "Sig[3].Value", "Sig[4].Value", "Sig[5].Value", "Sig[6].Value", "Sig[7].Value",
            "DynamicSig[1].Value", "DynamicSig[2].Value", "LoadedModule[", "AppPath",
            "ReportType", "FriendlyEventName", "OriginalFilename", "Response"]
    for line in txt.splitlines():
        if any(k in line for k in ("Sig[", "DynamicSig", "AppPath", "ReportType",
                                    "FriendlyEventName", "OriginalFilename",
                                    "NsAppName", "AppSessionGuid")):
            print(" ", line.strip())
