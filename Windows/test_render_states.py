import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

from settings import PetSettings, SettingsRepository
from eva_window import EvaWindow, PetAction


def render_state(action: PetAction, name: str):
    app = QApplication.instance() or QApplication([])
    settings = PetSettings()
    settings.metricsEnabled = False
    repo = SettingsRepository()
    window = EvaWindow(settings, repo)
    window.state.current_action = action
    window.state.target_action = action
    window.state.transition_progress = 1.0
    window.state.time = 3.0
    window.update()

    pixmap = QPixmap(window.size())
    pixmap.fill(Qt.GlobalColor.transparent)
    window.render(pixmap)
    out_path = os.path.join(os.path.dirname(__file__), f"render_{name}.png")
    pixmap.save(out_path)
    print(f"Saved {out_path}")


def main():
    for action in PetAction:
        render_state(action, action.value)


if __name__ == "__main__":
    main()
