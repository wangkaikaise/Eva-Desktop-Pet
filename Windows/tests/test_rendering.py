import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication

from eva_window import EvaWindow, resolve_metrics_font_family
from settings import PetSettings, SettingsRepository, metrics_font_family
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
    families = QFontDatabase.families()
    family = families[0] if families else "Segoe UI"
    updated = PetSettings(
        metricsEnabled=True,
        metricsFont=family,
        metricsFontSize=15,
        startOnLogin=False,
    )
    window._on_settings_applied(updated, [])
    font = window._metrics_font()
    assert font.pointSize() == 15
    assert resolve_metrics_font_family("Chosen Font", {"Chosen Font"}, "Fallback") == "Chosen Font"
    assert resolve_metrics_font_family("Missing Font", set(), "Fallback") == "Fallback"


def test_settings_dialog_collects_system_font_and_size(app):
    dialog = SettingsDialog(PetSettings(), [])
    families = QFontDatabase.families()
    if families:
        dialog.combo_font.setCurrentFont(QFont(families[0]))
    selected_family = dialog.combo_font.currentFont().family()
    dialog.spin_font_size.setValue(16)
    dialog._collect()
    assert dialog.settings.metricsFont == metrics_font_family(selected_family)
    assert dialog.settings.metricsFontSize == 16
    dialog.close()


def test_eye_transition_draws_one_symmetric_path_per_eye(window, monkeypatch):
    window.state.current_action = PetAction.CHEER
    window.state.target_action = PetAction.SLEEP
    window.state.transition_progress = 0.5
    calls = []

    def record_arc(painter, rect, color, opacity, width, height, glow=1.0,
                   curve_down=False):
        calls.append((QRectF(rect), opacity, height, curve_down))

    monkeypatch.setattr(window, "_draw_eye_mac_arc", record_arc)
    image = QImage(240, 100, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    window._draw_eyes(
        painter,
        QRectF(50, 35, 45, 24),
        QRectF(145, 35, 45, 24),
        None,
        QColor("#80C8EE"),
        1.0,
    )
    painter.end()

    assert len(calls) == 2
    assert calls[0][1:] == calls[1][1:]
    assert calls[0][2] >= 0.16
