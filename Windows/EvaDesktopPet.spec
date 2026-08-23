# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

datas = [('assets', 'assets')]
if Path('vendor').is_dir():
    datas.append(('vendor', 'vendor'))


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    # pythonnet(clr) 已默认禁用（跨线程加载 .NET 运行时是 0xc0000409 崩溃源），
    # 不再打包；温度读取走提权助手 / PowerShell 子进程
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EvaDesktopPet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 压缩 Qt DLL 是杀软误报的常见来源，商用分发关闭
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icons/eva-app.ico'],
    version='version_info.txt',
    # 基础功能不要求管理员权限；CPU 温度扩展仅在用户主动启用时单独提权。
    uac_admin=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='EvaDesktopPet',
)
