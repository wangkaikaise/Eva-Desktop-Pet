import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QImage
from PySide6.QtWidgets import QApplication

from eva_window import EvaWindow
from settings import PetSettings, SettingsRepository
from settings_dialog import SettingsDialog
from state_machine import PetAction


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    settings = PetSettings(metricsEnabled=False, startOnLogin=False)
    pet = EvaWindow(settings, SettingsRepository("EvaDesktopPetTests"))
    yield pet
    pet.anim_timer.stop()
    pet.metrics_timer.stop()
    pet.reminder_timer.stop()
    pet.tray.hide()
    pet.close()


def _render(window):
    image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    window.render(image)
    return image


def test_all_states_render_non_empty(window):
    for action in PetAction:
        window.state.current_action = action
        window.state.target_action = action
        window.state.transition_progress = 1.0
        image = _render(window)
        assert not image.isNull()
        assert image.size() == window.size()


def test_play_shape_remains_draggable(window):
    window.state.current_action = PetAction.PLAY
    window.state.target_action = PetAction.PLAY
    window.state.transition_progress = 1.0
    window._update_body_hit()
    pose = window.state.get_current_pose()
    assert window._hit_test(window._body_rect(pose).center().toPoint())


def test_settings_rebind_state_and_metrics(window):
    updated = PetSettings(animationSpeed=0.55, metricsEnabled=False)
    window._on_settings_applied(updated, [])
    assert window.state.settings is updated
    assert window.metrics.settings is updated


def test_metrics_font_family_and_size_are_applied(window):
    family = QFontDatabase.families()[0]
    updated = PetSettings(
        metricsEnabled=True,
        metricsFont=family,
        metricsFontSize=15,
        startOnLogin=False,
    )
    window._on_settings_applied(updated, [])
    font = window._metrics_font()
    assert font.family() == family
    assert font.pointSize() == 15


def test_settings_dialog_collects_system_font_and_size(app):
    family = QFontDatabase.families()[0]
    dialog = SettingsDialog(PetSettings(), [])
    dialog.combo_font.setCurrentFont(QFont(family))
    dialog.spin_font_size.setValue(16)
    dialog._collect()
    assert dialog.settings.metricsFont == family
    assert dialog.settings.metricsFontSize == 16
    dialog.close()
