import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time

try:
    import psutil
    HAS_PSUTIL = True
except Exception:
    HAS_PSUTIL = False

# 窗口模式下子进程不弹控制台黑框
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# ---------------------------------------------------------------------------
# CPU 温度：LibreHardwareMonitor（PawnIO 驱动版）进程内直读 AMD/Intel 传感器
# 说明：主板 ACPI 不暴露温度区时，WMI 拿不到 CPU 温度，必须读 SMU/MSR，
# 这需要内核驱动。WinRing0 被微软漏洞驱动黑名单拦截（内存完整性开启时），
# 因此使用 PawnIO 驱动（正规签名）配套的 LHM 分支。
# ---------------------------------------------------------------------------
_LHM_LOCK = threading.Lock()
_LHM_STATE = {"computer": None, "temp_type": None, "failed": False}


def select_cpu_temperature(readings, temp_mode="avg"):
    """Select a CPU temperature with the same semantics as core monitors.

    Average mode prefers LHM's dedicated Core/CCD Average sensor, then actual
    per-core sensors, then AMD's die sensor, and only then all CPU readings.
    This avoids averaging duplicate aggregate sensors or reporting the package
    hotspot as though it were the normal core average.
    """
    valid = []
    for name, raw_value in readings:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if 0 < value < 120:
            valid.append((str(name or ""), value))
    if not valid:
        return None
    if temp_mode != "avg":
        return max(value for _, value in valid)

    averages = [
        value for name, value in valid
        if "core average" in name.lower() or "ccds average" in name.lower()
    ]
    per_core = [
        value for name, value in valid
        if (name.lower().startswith(("p-core #", "e-core #", "cpu core #", "core #"))
            and "distance" not in name.lower())
    ]
    die = [
        value for name, value in valid
        if name.lower() in ("core (tctl/tdie)", "core (tdie)")
    ]
    selected = averages or per_core or die or [value for _, value in valid]
    return round(sum(selected) / len(selected), 1)


def _vendor_dir():
    """vendor DLL 目录：源码运行与 PyInstaller 打包后都能找到。"""
    if hasattr(sys, "_MEIPASS"):  # PyInstaller 解包目录
        d = os.path.join(sys._MEIPASS, "vendor")
        if os.path.isdir(d):
            return d
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")


def lhm_available():
    """PawnIO 驱动是否已安装（服务存在）。"""
    try:
        r = subprocess.run(["sc", "query", "PawnIO"], capture_output=True, timeout=5,
                           creationflags=_NO_WINDOW)
        return b"PAWNIO" in (r.stdout or b"").upper()
    except Exception:
        return False


def ensure_pawnio_driver():
    """检查 PawnIO 驱动是否已安装，未安装则从 vendor 目录自动安装。
    需要管理员权限。返回 (是否已安装, 提示消息)。
    """
    if lhm_available():
        return True, "PawnIO 驱动已安装"

    # 查找 PawnIO_setup.exe
    vendor_dir = _vendor_dir()
    setup_path = os.path.join(vendor_dir, "PawnIO_setup.exe")
    if not os.path.exists(setup_path):
        return False, "未找到 PawnIO_setup.exe"

    if not is_admin():
        return False, "需要管理员权限安装驱动"

    try:
        # PawnIO_setup.exe 参数格式：[-install] [-uninstall] [-unrestricted] [-debuginfo] [-silent]
        # 静默安装用 -install -silent，失败再尝试交互式 -install / 默认安装。
        # 设置工作目录为 vendor 目录，有些安装程序需要在同目录操作。
        for args in [["-install", "-silent"], ["-install"], []]:
            try:
                subprocess.run(
                    [setup_path] + args,
                    capture_output=True, timeout=30,
                    creationflags=_NO_WINDOW,
                    cwd=vendor_dir,
                )
            except subprocess.TimeoutExpired:
                continue
            # 安装后等待服务出现
            for _ in range(10):
                import time as _t
                _t.sleep(1)
                if lhm_available():
                    return True, "PawnIO 驱动安装成功"
        return False, "PawnIO 驱动安装后服务未出现"
    except subprocess.TimeoutExpired:
        return False, "PawnIO 安装超时"
    except Exception as e:
        return False, f"PawnIO 安装失败: {e}"


def _lhm_ps_script(temp_mode="avg"):
    """生成读取 CPU 温度的 PowerShell 脚本，直接 .NET 互操作加载 LHM DLL。
    temp_mode: "max" 取最高温度，"avg" 取平均温度。
    """
    vendor_dir = _vendor_dir()
    dll_path = os.path.join(vendor_dir, "LibreHardwareMonitorLib.dll")
    if not os.path.exists(dll_path):
        return None
    ps_path = dll_path.replace("'", "''")
    vd = vendor_dir.replace("'", "''")
    mode = temp_mode if temp_mode in ("max", "avg") else "avg"
    return f'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {{
    # LoadFrom 会把 DLL 所在目录作为依赖搜索路径
    # 先按依赖顺序预加载（依赖 → 主库）
    $deps = @(
        'System.Runtime.CompilerServices.Unsafe',
        'System.Buffers',
        'System.Numerics.Vectors',
        'System.Memory'
    )
    foreach ($dep in $deps) {{
        $p = Join-Path '{vd}' "$dep.dll"
        if (Test-Path $p) {{
            try {{ [System.Reflection.Assembly]::LoadFrom($p) | Out-Null }} catch {{}}
        }}
    }}
    $asm = [System.Reflection.Assembly]::LoadFrom('{ps_path}')
    $computerType = $asm.GetType('LibreHardwareMonitor.Hardware.Computer')
    if ($computerType -eq $null) {{ Write-Output "ERROR"; exit }}
    $computer = [Activator]::CreateInstance($computerType)
    $computer.IsCpuEnabled = $true
    $computer.Open()
    $sensorType = $asm.GetType('LibreHardwareMonitor.Hardware.SensorType')
    $tempType = [Enum]::Parse($sensorType, 'Temperature')
    $allTemps = [System.Collections.Generic.List[double]]::new()
    $averageTemps = [System.Collections.Generic.List[double]]::new()
    $coreTemps = [System.Collections.Generic.List[double]]::new()
    $dieTemps = [System.Collections.Generic.List[double]]::new()
    foreach ($hw in $computer.Hardware) {{
        try {{ $hw.Update() }} catch {{ continue }}
        foreach ($sensor in $hw.Sensors) {{
            if ($sensor.SensorType -eq $tempType) {{
                $v = $sensor.Value
                if ($v -ne $null -and $v -gt 0 -and $v -lt 120) {{
                    $allTemps.Add([double]$v)
                    $name = [string]$sensor.Name
                    if ($name -ieq 'Core Average' -or $name -ieq 'CCDs Average (Tdie)') {{
                        $averageTemps.Add([double]$v)
                    }} elseif ($name -match '^(P-Core #|E-Core #|CPU Core #|Core #)' -and $name -notmatch 'Distance') {{
                        $coreTemps.Add([double]$v)
                    }} elseif ($name -ieq 'Core (Tctl/Tdie)' -or $name -ieq 'Core (Tdie)') {{
                        $dieTemps.Add([double]$v)
                    }}
                }}
            }}
        }}
    }}
    $computer.Close()
    $mode = '{mode}'
    $selectedTemps = $allTemps
    if ($averageTemps.Count -gt 0) {{ $selectedTemps = $averageTemps }}
    elseif ($coreTemps.Count -gt 0) {{ $selectedTemps = $coreTemps }}
    elseif ($dieTemps.Count -gt 0) {{ $selectedTemps = $dieTemps }}
    if ($mode -eq 'avg' -and $selectedTemps.Count -gt 0) {{
        $sum = 0.0
        foreach ($t in $selectedTemps) {{ $sum += $t }}
        Write-Output ([math]::Round($sum / $selectedTemps.Count, 1))
    }} else {{
        # max 模式：直接取所有传感器最大值
        $maxVal = 0.0
        foreach ($t in $allTemps) {{ if ($t -gt $maxVal) {{ $maxVal = $t }} }}
        if ($maxVal -gt 0) {{ Write-Output $maxVal }} else {{ Write-Output "NONE" }}
    }}
}} catch {{
    Write-Output "ERROR"
}}
'''


def _lhm_read_temperature(temp_mode="avg"):
    """读取 CPU 温度（°C）。
    优先级：管理员温度助手文件 → 进程内 pythonnet 直读 → PowerShell 子进程。
    三种方式都失败则返回 None。
    temp_mode: "max" 取最高温度，"avg" 取平均温度。
    """
    # ---- 方式零：管理员温度助手（后台常驻提权进程写入的文件） ----
    # 助手会读取 mode 文件动态切换，这里只需读结果
    status, val = helper_status()
    if status == "alive" and val:
        return val
    # ---- 方式一：进程内 pythonnet（默认禁用） ----
    # .NET CLR 在 QThreadPool 线程加载后，GC/关闭回调与原生窗口代码
    # 交互可能触发 fail-fast 崩溃（0xc0000409，无 Python 异常痕迹）。
    # 非提权进程内直读本就拿不到温度（PawnIO 需要管理员），
    # 温度统一走提权助手/PowerShell 子进程，无功能损失。
    # 调试需要时可设环境变量 EVA_ENABLE_INPROCESS_CLR=1 重新启用。
    _inprocess_clr_enabled = os.environ.get("EVA_ENABLE_INPROCESS_CLR") == "1"
    with _LHM_LOCK:
        if not _LHM_STATE["failed"] and _inprocess_clr_enabled:
            try:
                if _LHM_STATE["computer"] is None:
                    vd = _vendor_dir()
                    if vd not in sys.path:
                        sys.path.insert(0, vd)
                    import clr
                    clr.AddReference("LibreHardwareMonitorLib")
                    from LibreHardwareMonitor import Hardware
                    computer = Hardware.Computer()
                    computer.IsCpuEnabled = True
                    computer.Open()
                    _LHM_STATE["computer"] = computer
                    _LHM_STATE["temp_type"] = Hardware.SensorType.Temperature
                computer = _LHM_STATE["computer"]
                temp_type = _LHM_STATE["temp_type"]
                readings = []
                for hw in computer.Hardware:
                    try:
                        hw.Update()
                    except Exception:
                        continue
                    for s in hw.Sensors:
                        if s.SensorType != temp_type or s.Value is None:
                            continue
                        try:
                            v = float(s.Value)
                        except Exception:
                            continue
                        if not (0 < v < 120):
                            continue
                        readings.append((str(s.Name), v))
                result = select_cpu_temperature(readings, temp_mode)
                if result is not None:
                    return result
            except Exception:
                _LHM_STATE["failed"] = True

    # ---- 方式二：PowerShell 子进程加载 LHM DLL（打包后主用方案） ----
    # 如果 PawnIO 驱动未安装，跳过慢速的 PowerShell 子进程调用（10秒超时），
    # 交给 _auto_start_temp_helper 中的 ensure_pawnio_driver() 先安装驱动。
    if not lhm_available():
        return None
    return _lhm_read_temperature_ps(temp_mode)


def _lhm_read_temperature_ps(temp_mode="avg"):
    """通过 PowerShell 子进程加载 LHM DLL 读取 CPU 温度。
    不依赖 pythonnet，PyInstaller 打包后也能正常工作。
    temp_mode: "max" 取最高温度，"avg" 取平均温度。
    """
    script = _lhm_ps_script(temp_mode)
    if script is None:
        return None
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=10,
            creationflags=_NO_WINDOW,
            encoding="utf-8", errors="replace",
        )
        txt = (result.stdout or "").strip()
        if not txt or txt in ("ERROR", "NONE"):
            return None
        v = float(txt)
        if 0 < v < 120:
            return v
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 管理员温度助手：
# PawnIO 驱动的 SMU/MSR 访问需要提权，非提权进程读到的 CPU 温度恒为 0。
# 方案：通过 UAC 启动一个常驻的提权 PowerShell 进程，循环把温度写入
# TEMP 下的数据文件；宠物进程（非提权）直接读该文件。宠物进程同时写
# 心跳文件，宠物退出后心跳超时，助手自动结束，不留驻留进程。
# ---------------------------------------------------------------------------
# v2 isolates the corrected average-sensor helper from a 13.3.1 helper that
# may still be alive for up to 90 seconds during an in-place upgrade. Reusing
# the old heartbeat/data paths would keep its obsolete averaging logic alive.
_HELPER_DIR = os.path.join(tempfile.gettempdir(), "eva_pet_helper_v2")
_HELPER_MAX_AGE = 15.0  # 数据文件超过该秒数视为过期


def _helper_paths():
    """返回 (温度数据文件, 心跳文件, 助手脚本文件, 错误文件)。"""
    return (
        os.path.join(_HELPER_DIR, "cpu_temp.txt"),
        os.path.join(_HELPER_DIR, "heartbeat.txt"),
        os.path.join(_HELPER_DIR, "helper.ps1"),
        os.path.join(_HELPER_DIR, "error.txt"),
    )


def _helper_mode_path():
    """温度模式文件路径（max/avg），助手每次循环读取该文件动态切换。"""
    return os.path.join(_HELPER_DIR, "temp_mode.txt")


def write_temp_mode(mode):
    """写入温度模式到文件，供运行中的助手动态切换。"""
    try:
        os.makedirs(_HELPER_DIR, exist_ok=True)
        with open(_helper_mode_path(), "w") as f:
            f.write("max" if mode == "max" else "avg")
    except Exception:
        pass


def touch_helper_heartbeat():
    """宠物进程心跳，供提权助手判断宠物是否还在运行。"""
    try:
        os.makedirs(_HELPER_DIR, exist_ok=True)
        _, hb, _, _ = _helper_paths()
        with open(hb, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def helper_status():
    """返回 (状态, 温度)。状态：alive=数据新鲜；stale=助手已死/数据过期；none=无数据文件。"""
    tf, _, _, _ = _helper_paths()
    if not os.path.exists(tf):
        return "none", None
    try:
        with open(tf) as f:
            raw = f.read().strip()
        if "|" not in raw:
            return "stale", None
        val_s, ts_s = raw.split("|", 1)
        val = float(val_s)
        ts = float(ts_s)
        if time.time() - ts <= _HELPER_MAX_AGE and 0 < val < 120:
            return "alive", val
        return "stale", None
    except Exception:
        return "stale", None


def helper_error():
    """读取助手脚本的错误信息（如果有）。返回错误字符串或 None。"""
    _, _, _, err_path = _helper_paths()
    if not os.path.exists(err_path):
        return None
    try:
        with open(err_path, encoding="utf-8", errors="replace") as f:
            msg = f.read().strip()
        return msg if msg else None
    except Exception:
        return None


def _lhm_helper_ps_script(temp_mode="avg"):
    """生成提权常驻助手的 PowerShell 脚本：循环读取温度写入数据文件。
    temp_mode: "max" 取所有 CPU 温度传感器最大值；"avg" 取平均值。
    """
    vendor_dir = _vendor_dir()
    dll_path = os.path.join(vendor_dir, "LibreHardwareMonitorLib.dll")
    if not os.path.exists(dll_path):
        return None
    ps_path = dll_path.replace("'", "''")
    vd = vendor_dir.replace("'", "''")
    setup_path = os.path.join(vendor_dir, "PawnIO_setup.exe").replace("'", "''")
    tf, hb, _, err_path = _helper_paths()
    tf_e = tf.replace("'", "''")
    hb_e = hb.replace("'", "''")
    err_e = err_path.replace("'", "''")
    # mode 文件路径：助手每次循环读取该文件，动态切换 max/avg 模式
    mode_file = os.path.join(_HELPER_DIR, "temp_mode.txt").replace("'", "''")
    # 默认模式写死到脚本中作为兜底
    default_mode = temp_mode if temp_mode in ("max", "avg") else "avg"
    return f'''
$ErrorActionPreference = 'Stop'
$computer = $null
$defaultMode = '{default_mode}'
$modeFile = '{mode_file}'
try {{
    # The helper already runs elevated. Install PawnIO here so a normal
    # non-admin Eva process never gets stuck before the UAC step.
    $pawnService = Get-Service -Name 'PawnIO' -ErrorAction SilentlyContinue
    if ($null -eq $pawnService) {{
        if (-not (Test-Path '{setup_path}')) {{
            throw 'PawnIO_setup.exe was not found in the application vendor directory.'
        }}
        $installer = Start-Process -FilePath '{setup_path}' `
            -ArgumentList @('-install', '-silent') -Wait -PassThru -WindowStyle Hidden
        if ($installer.ExitCode -ne 0 -and $installer.ExitCode -ne 3010) {{
            throw "PawnIO installation failed with exit code $($installer.ExitCode)."
        }}
        for ($i = 0; $i -lt 10; $i++) {{
            Start-Sleep -Milliseconds 500
            $pawnService = Get-Service -Name 'PawnIO' -ErrorAction SilentlyContinue
            if ($null -ne $pawnService) {{ break }}
        }}
        if ($null -eq $pawnService) {{
            throw 'PawnIO was installed but its Windows service is unavailable. A restart may be required.'
        }}
    }}
    $deps = @(
        'System.Runtime.CompilerServices.Unsafe',
        'System.Buffers',
        'System.Numerics.Vectors',
        'System.Memory'
    )
    foreach ($dep in $deps) {{
        $p = Join-Path '{vd}' "$dep.dll"
        if (Test-Path $p) {{
            try {{ [System.Reflection.Assembly]::LoadFrom($p) | Out-Null }} catch {{}}
        }}
    }}
    $asm = [System.Reflection.Assembly]::LoadFrom('{ps_path}')
    $computerType = $asm.GetType('LibreHardwareMonitor.Hardware.Computer')
    $sensorType = $asm.GetType('LibreHardwareMonitor.Hardware.SensorType')
    $tempType = [Enum]::Parse($sensorType, 'Temperature')
    $computer = [Activator]::CreateInstance($computerType)
    $computer.IsCpuEnabled = $true
    $computer.Open()
    $start = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    while ($true) {{
        # 读取当前模式（允许运行时切换）
        $mode = $defaultMode
        try {{
            if (Test-Path $modeFile) {{
                $m = (Get-Content $modeFile -Raw).Trim().ToLower()
                if ($m -eq 'avg' -or $m -eq 'average') {{ $mode = 'avg' }}
                else {{ $mode = 'max' }}
            }}
        }} catch {{}}

        # 收集所有 CPU 温度传感器值
        $allTemps = [System.Collections.Generic.List[double]]::new()
        $averageTemps = [System.Collections.Generic.List[double]]::new()
        $coreTemps = [System.Collections.Generic.List[double]]::new()
        $dieTemps = [System.Collections.Generic.List[double]]::new()
        foreach ($hw in $computer.Hardware) {{
            try {{ $hw.Update() }} catch {{ continue }}
            foreach ($sensor in $hw.Sensors) {{
                if ($sensor.SensorType -eq $tempType) {{
                    $v = $sensor.Value
                    if ($v -ne $null -and $v -gt 0 -and $v -lt 120) {{
                        $allTemps.Add([double]$v)
                        $name = [string]$sensor.Name
                        if ($name -ieq 'Core Average' -or $name -ieq 'CCDs Average (Tdie)') {{
                            $averageTemps.Add([double]$v)
                        }} elseif ($name -match '^(P-Core #|E-Core #|CPU Core #|Core #)' -and $name -notmatch 'Distance') {{
                            $coreTemps.Add([double]$v)
                        }} elseif ($name -ieq 'Core (Tctl/Tdie)' -or $name -ieq 'Core (Tdie)') {{
                            $dieTemps.Add([double]$v)
                        }}
                    }}
                }}
            }}
        }}
        # 按模式计算
        $val = 0.0
        $selectedTemps = $allTemps
        if ($averageTemps.Count -gt 0) {{ $selectedTemps = $averageTemps }}
        elseif ($coreTemps.Count -gt 0) {{ $selectedTemps = $coreTemps }}
        elseif ($dieTemps.Count -gt 0) {{ $selectedTemps = $dieTemps }}
        if ($mode -eq 'avg' -and $selectedTemps.Count -gt 0) {{
            $sum = 0.0
            foreach ($t in $selectedTemps) {{ $sum += $t }}
            $val = [math]::Round($sum / $selectedTemps.Count, 1)
        }} else {{
            # max 模式：直接取所有传感器最大值
            foreach ($t in $allTemps) {{ if ($t -gt $val) {{ $val = $t }} }}
        }}
        $ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        "$val|$ts" | Set-Content -Path '{tf_e}' -Encoding ASCII
        # Pet heartbeat check: if the pet process is gone (90 seconds without a heartbeat), the assistant automatically exits
        $hbOk = $false
        if (Test-Path '{hb_e}') {{
            try {{
                $hbTs = [double]((Get-Content '{hb_e}' -Raw).Trim())
                if ($ts - $hbTs -lt 90) {{ $hbOk = $true }}
            }} catch {{}}
        }}
        if (-not $hbOk -and (($ts - $start) -gt 90)) {{ break }}
        Start-Sleep -Seconds 2
    }}
}} catch {{
    try {{
        $err = $_.Exception.Message
        "$err" | Set-Content -Path '{err_e}' -Encoding UTF8
    }} catch {{}}
}} finally {{
    if ($computer -ne $null) {{ try {{ $computer.Close() }} catch {{}} }}
}}
'''


def is_admin():
    """当前进程是否以管理员权限运行。"""
    if os.name != "nt":
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def start_elevated_temp_helper(temp_mode="avg"):
    """启动常驻温度读取助手。
    已有管理员权限时直接启动（免 UAC），否则通过 UAC 提权启动。
    temp_mode: "max" 显示最高温度，"avg" 显示平均温度。
    返回 (是否启动成功, 提示消息)。
    """
    if os.name != "nt":
        return False, "仅支持 Windows"
    tf, hb, script_path, err_path = _helper_paths()
    script = _lhm_helper_ps_script(temp_mode)
    if script is None:
        return False, "未找到 LibreHardwareMonitorLib.dll"
    try:
        os.makedirs(_HELPER_DIR, exist_ok=True)
        touch_helper_heartbeat()
        # 写入温度模式文件，助手启动时读取
        write_temp_mode(temp_mode)
        # 清除旧错误文件
        for p in (err_path,):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        # utf-8-sig：PowerShell 5.1 需要 BOM 才按 UTF-8 解析
        with open(script_path, "w", encoding="utf-8-sig") as f:
            f.write(script)
        # 清掉旧数据文件，等待新数据
        if os.path.exists(tf):
            os.remove(tf)

        if is_admin():
            # 已有管理员权限：子进程继承提权令牌，直接启动无需 UAC
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-File", script_path],
                creationflags=_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            # 非提权：通过 UAC 弹窗提权启动
            params = (
                f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
                f'-File "{script_path}"'
            )
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "powershell.exe", params, None, 0
            )
            if rc <= 32:
                return False, "管理员授权被取消"
    except Exception as e:
        return False, f"启动失败: {e}"
    return True, "温度助手已启动"


def shutdown_lhm():
    """退出时释放 LHM（内核驱动由系统管理，无需卸载）。"""
    with _LHM_LOCK:
        try:
            if _LHM_STATE["computer"] is not None:
                _LHM_STATE["computer"].Close()
        except Exception:
            pass
        _LHM_STATE["computer"] = None


class MetricsCollector:
    """采集 CPU/GPU 性能指标。
    - CPU 占用：psutil
    - CPU 温度：LibreHardwareMonitor（PawnIO 驱动）直读 SMU/MSR 传感器；
      首次启用时由独立提权助手安装驱动，主程序始终保持普通权限
    - GPU 占用/温度：nvidia-smi（NVIDIA 显卡）
    """

    def __init__(self, settings):
        self.settings = settings
        self._first_cpu = True
        self._nvidia_available = None
        self._nvidia_fail_count = 0
        self._last_nvidia = (None, None)
        self._last_nvidia_time = 0.0
        self._nvidia_cache_seconds = 0.9
        self._cpu_temp_value = None
        self._cpu_temp_time = 0.0
        self._cpu_temp_cache_seconds = 2.0
        self._lhm_tried = False
        self._generic_gpu_value = None
        self._generic_gpu_time = 0.0
        self._generic_gpu_cache_seconds = 2.0

    def sample(self):
        return {
            "cpu": self._cpu_percent() if self.settings.metricsShowCpu else None,
            "cpu_temp": self._cpu_temp() if self.settings.metricsShowCpuTemp else None,
            "gpu": self._gpu_percent() if self.settings.metricsShowGpu else None,
            "gpu_temp": self._gpu_temp() if self.settings.metricsShowGpuTemp else None,
        }

    def _cpu_percent(self):
        if not HAS_PSUTIL:
            return None
        try:
            if self._first_cpu:
                # 第一次调用初始化基准
                psutil.cpu_percent(interval=None)
                self._first_cpu = False
                return 0.0
            return psutil.cpu_percent(interval=None)
        except Exception:
            return None

    def reset_temp_cache(self):
        """清空温度缓存，强制下次采样立即重新读取（管理员助手启动后调用）。"""
        self._cpu_temp_value = None
        self._cpu_temp_time = 0.0

    def _cpu_temp(self):
        now = time.time()
        if now - self._cpu_temp_time < self._cpu_temp_cache_seconds:
            return self._cpu_temp_value
        # 心跳：供提权温度助手判断宠物进程是否存活
        touch_helper_heartbeat()
        # 温度模式：从设置中读取（max 或 avg）
        temp_mode = getattr(self.settings, "metricsCpuTempMode", "avg")
        # 优先：LibreHardwareMonitor 直读 SMU（需 PawnIO 驱动）
        value = _lhm_read_temperature(temp_mode)
        self._cpu_temp_value = value
        self._cpu_temp_time = now
        # 成功则 20 秒刷新一次；不可用则 2 分钟后才重试
        self._cpu_temp_cache_seconds = 20.0 if value is not None else 120.0
        return value

    def _cpu_temp_wmi(self):
        value = None
        try:
            # wmic 在 Win11 24H2 已移除，改用 PowerShell CIM
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance -Namespace root/wmi -ClassName "
                    "MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue | "
                    "Measure-Object -Property CurrentTemperature -Average).Average",
                ],
                capture_output=True, text=True, timeout=8,
                creationflags=_NO_WINDOW,
            )
            txt = (result.stdout or "").strip()
            if txt and txt.lower() not in ("", "n/a", "nan"):
                kelvin_tenths = float(txt)
                celsius = kelvin_tenths / 10.0 - 273.15
                if 0 <= celsius <= 120:
                    value = round(celsius, 1)
        except Exception:
            value = None
        return value

    def _nvidia_info(self):
        """返回 (utilization, temperature)；缓存约 1 秒避免频繁子进程。"""
        if self._nvidia_available is False:
            return None, None
        now = time.time()
        if now - self._last_nvidia_time < self._nvidia_cache_seconds:
            return self._last_nvidia
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True, text=True, timeout=3,
                creationflags=_NO_WINDOW,
            )
            if result.returncode == 0 and result.stdout.strip():
                self._nvidia_available = True
                self._nvidia_fail_count = 0
                line = result.stdout.strip().splitlines()[0]
                parts = [p.strip() for p in line.split(",")]
                util = float(parts[0]) if len(parts) > 0 and parts[0] else None
                temp = float(parts[1]) if len(parts) > 1 and parts[1] else None
                self._last_nvidia = (util, temp)
                self._last_nvidia_time = now
                return util, temp
            # 命令存在但返回失败：可能是瞬时驱动问题，连续 3 次失败才判死
            self._nvidia_fail_count += 1
            if self._nvidia_fail_count >= 3:
                self._nvidia_available = False
        except FileNotFoundError:
            # 机器上没装 nvidia-smi（非 N 卡或没驱动），永久禁用
            self._nvidia_available = False
        except Exception:
            # 超时等瞬时错误：保留重试机会
            self._nvidia_fail_count += 1
            if self._nvidia_fail_count >= 5:
                self._nvidia_available = False
        # 失败路径：更新时间戳限流重试，保留上一次已知值避免闪烁
        self._last_nvidia_time = now
        if self._nvidia_available is not True:
            self._last_nvidia = (None, None)
        return self._last_nvidia

    def _gpu_percent(self):
        nvidia_value = self._nvidia_info()[0]
        if nvidia_value is not None:
            return max(0.0, min(100.0, nvidia_value))
        return self._generic_gpu_percent()

    def _generic_gpu_percent(self):
        """通过 Windows GPU Performance Counters 支持 AMD/Intel/NVIDIA。

        只在 nvidia-smi 不可用时后台调用；结果按缓存限流，不进入绘制线程。
        """
        if os.name != "nt":
            return None
        now = time.time()
        if now - self._generic_gpu_time < self._generic_gpu_cache_seconds:
            return self._generic_gpu_value
        script = (
            "$v=Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine "
            "-ErrorAction SilentlyContinue | Where-Object {$_.Name -match "
            "'engtype_(3D|Graphics|Compute)'} | Select-Object -ExpandProperty "
            "UtilizationPercentage; if($v){[math]::Min(100,($v|Measure-Object "
            "-Sum).Sum)}"
        )
        value = None
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW,
            )
            lines = (result.stdout or "").strip().splitlines()
            if lines:
                parsed = float(lines[-1].strip())
                if 0.0 <= parsed <= 100.0:
                    value = parsed
        except Exception:
            value = None
        self._generic_gpu_value = value
        self._generic_gpu_time = now
        return value

    def _gpu_temp(self):
        return self._nvidia_info()[1]
