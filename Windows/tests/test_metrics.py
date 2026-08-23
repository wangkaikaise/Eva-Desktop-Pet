from pathlib import Path

import metrics


def test_elevated_helper_installs_driver_before_opening_monitor(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "LibreHardwareMonitorLib.dll").touch()
    (vendor / "PawnIO_setup.exe").touch()
    monkeypatch.setattr(metrics, "_vendor_dir", lambda: str(vendor))

    script = metrics._lhm_helper_ps_script("max")

    assert script is not None
    assert "Get-Service -Name 'PawnIO'" in script
    assert "PawnIO_setup.exe" in script
    assert script.index("Start-Process -FilePath") < script.index("Assembly]::LoadFrom")
    assert "'-install', '-silent'" in script


def test_temperature_script_requires_bundled_lhm(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "_vendor_dir", lambda: str(Path(tmp_path)))
    assert metrics._lhm_helper_ps_script("max") is None


def test_average_temperature_prefers_lhm_core_average_over_hotspot():
    readings = [
        ("CPU Package", 64.0),
        ("Core Max", 62.0),
        ("Core Average", 47.0),
        ("P-Core #1", 45.0),
        ("P-Core #2", 49.0),
    ]
    assert metrics.select_cpu_temperature(readings, "avg") == 47.0
    assert metrics.select_cpu_temperature(readings, "max") == 64.0


def test_average_temperature_falls_back_to_real_per_core_sensors():
    readings = [
        ("CPU Package", 63.0),
        ("P-Core #1", 42.0),
        ("P-Core #2", 48.0),
        ("P-Core #1 Distance to TjMax", 58.0),
    ]
    assert metrics.select_cpu_temperature(readings, "avg") == 45.0


def test_average_temperature_prefers_amd_die_over_control_hotspot():
    readings = [("Core (Tctl)", 66.0), ("Core (Tdie)", 49.0)]
    assert metrics.select_cpu_temperature(readings, "avg") == 49.0
