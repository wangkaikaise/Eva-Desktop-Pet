import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

from settings import PetSettings, SettingsRepository
from eva_window import EvaWindow


def main():
    app = QApplication([])
    settings = PetSettings()
    settings.metricsEnabled = True
    settings.shieldEnabled = True
    settings.shieldStyle = "halo"
    repo = SettingsRepository()
    window = EvaWindow(settings, repo)
    window.state.set_action(window.state.current_action)
    window.state.time = 3.0
    window.update()

    pixmap = QPixmap(window.size())
    pixmap.fill(Qt.GlobalColor.transparent)
    window.render(pixmap)
    out_path = os.path.join(os.path.dirname(__file__), "render_test.png")
    pixmap.save(out_path)
    print(f"Saved render to {out_path}")


if __name__ == "__main__":
    main()
