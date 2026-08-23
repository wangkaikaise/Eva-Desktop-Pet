import json
import os
from dataclasses import dataclass, asdict
from typing import List


_LEGACY_METRICS_FONTS = {
    "rounded": "Microsoft YaHei UI",
    "system": "Segoe UI",
    "monospace": "Consolas",
}


def metrics_font_family(value: str) -> str:
    """Return a real font family, migrating the three legacy preset tokens."""
    value = str(value or "").strip()
    return _LEGACY_METRICS_FONTS.get(value, value or "Microsoft YaHei UI")


@dataclass
class PetReminder:
    id: str = ""
    title: str = ""
    schedule: str = "daily"  # 'daily' or 'interval'
    hour: int = 9
    minute: int = 0
    intervalMinutes: int = 60
    isEnabled: bool = True

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PetSettings:
    schemaVersion: int = 2
    size: int = 220
    opacity: float = 1.0
    animationSpeed: float = 0.8
    alwaysOnTop: bool = True
    startOnLogin: bool = False
    lightPoolBrightness: float = 0.72
    shieldEnabled: bool = False
    shieldStyle: str = "halo"  # halo, bubble, orbit
    mood: str = "calm"
    moodAutoSwitch: bool = True
    moodIntervalMinutes: int = 30
    metricsEnabled: bool = False
    metricsPosition: str = "right"  # top, bottom, left, right
    metricsRefreshSeconds: int = 5
    metricsShowCpu: bool = True
    metricsShowCpuTemp: bool = True
    metricsCpuTempMode: str = "avg"  # avg matches core-monitoring tools; max is hotspot
    metricsShowGpu: bool = True
    metricsShowGpuTemp: bool = True
    metricsBackgroundOpacity: float = 0.28
    metricsContentOpacity: float = 1.0
    metricsFont: str = "Microsoft YaHei UI"
    metricsFontSize: int = 10
    metricsTextColor: str = "white"  # white, blue, black
    language: str = "zh"

    def clamp(self):
        self.size = max(140, min(520, (self.size // 10) * 10))
        self.opacity = max(0.55, min(1.0, self.opacity))
        self.animationSpeed = max(0.4, min(1.0, round(self.animationSpeed / 0.05) * 0.05))
        self.lightPoolBrightness = max(0.1, min(1.0, round(self.lightPoolBrightness / 0.05) * 0.05))
        self.moodIntervalMinutes = max(15, min(60, self.moodIntervalMinutes))
        self.metricsRefreshSeconds = 5 if self.metricsRefreshSeconds not in (2, 5, 10) else self.metricsRefreshSeconds
        self.metricsCpuTempMode = self.metricsCpuTempMode if self.metricsCpuTempMode in ("avg", "max") else "avg"
        self.metricsBackgroundOpacity = max(0.0, min(0.75, self.metricsBackgroundOpacity))
        self.metricsContentOpacity = max(0.25, min(1.0, self.metricsContentOpacity))
        self.metricsFont = metrics_font_family(self.metricsFont)
        self.metricsFontSize = max(8, min(18, int(self.metricsFontSize)))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        fields = cls.__dataclass_fields__
        filtered = {k: v for k, v in d.items() if k in fields}
        # 13.3.1 and earlier defaulted to the hottest package/core sensor. Existing
        # users therefore saw a value 10–20 °C above tools that show core average.
        # Migrate that old default once; users can still select "highest" afterwards.
        if int(d.get("schemaVersion", 1) or 1) < 2:
            filtered["metricsCpuTempMode"] = "avg"
            filtered["schemaVersion"] = 2
        return cls(**filtered)


class SettingsRepository:
    def __init__(self, app_name="EvaDesktopPet"):
        self.base_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), app_name)
        os.makedirs(self.base_dir, exist_ok=True)
        self.settings_path = os.path.join(self.base_dir, "settings.json")
        self.reminders_path = os.path.join(self.base_dir, "reminders.json")

    def load_settings(self) -> PetSettings:
        if os.path.exists(self.settings_path):
            try:
                with open(self.settings_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                s = PetSettings.from_dict(data)
                s.clamp()
                return s
            except Exception:
                pass
        return PetSettings()

    def save_settings(self, settings: PetSettings):
        tmp = self.settings_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.settings_path)

    def load_reminders(self) -> List[PetReminder]:
        if os.path.exists(self.reminders_path):
            try:
                with open(self.reminders_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                reminders = []
                for item in data:
                    r = PetReminder.from_dict(item)
                    if not r.schedule:
                        r.schedule = "daily"
                    reminders.append(r)
                return reminders
            except Exception:
                pass
        return []

    def save_reminders(self, reminders: List[PetReminder]):
        tmp = self.reminders_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([r.to_dict() for r in reminders], f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.reminders_path)
