import json

from settings import PetSettings, SettingsRepository, metrics_font_family


def test_settings_round_trip_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    repo = SettingsRepository("EvaDesktopPetTests")
    settings = PetSettings(size=330, metricsBackgroundOpacity=0.0)
    repo.save_settings(settings)
    assert repo.load_settings().size == 330
    assert not (tmp_path / "EvaDesktopPetTests" / "settings.json.tmp").exists()
    json.loads((tmp_path / "EvaDesktopPetTests" / "settings.json").read_text("utf-8"))


def test_settings_clamp_ranges():
    settings = PetSettings(
        size=999,
        opacity=-1,
        metricsBackgroundOpacity=9,
        metricsFont="rounded",
        metricsFontSize=99,
    )
    settings.clamp()
    assert settings.size == 520
    assert settings.opacity == 0.55
    assert settings.metricsBackgroundOpacity == 0.75
    assert settings.metricsFont == "Microsoft YaHei UI"
    assert settings.metricsFontSize == 18


def test_real_metrics_font_family_is_preserved():
    assert metrics_font_family("Segoe UI") == "Segoe UI"
    assert metrics_font_family("monospace") == "Consolas"
