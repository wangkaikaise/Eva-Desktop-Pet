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
