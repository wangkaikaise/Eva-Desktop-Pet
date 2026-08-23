import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QColor, QPainter
from PySide6.QtCore import Qt, QSize, QPoint, QRect

from settings import PetSettings, SettingsRepository
from eva_window import EvaWindow


def main():
    app = QApplication([])
    settings = PetSettings()
    settings.size = 400
    settings.opacity = 1.0
    settings.metricsEnabled = False
    settings.shieldEnabled = False
    repo = SettingsRepository()
    window = EvaWindow(settings, repo)

    from state_machine import PetAction
    window.state.set_action(PetAction.IDLE)
    window.state.time = 3.0
    window.update()

    # 4x 放大渲染
    base_size = window.size()
    scale = 4
    target = QSize(int(base_size.width() * scale), int(base_size.height() * scale))
    full = QPixmap(target)
    full.fill(QColor(0, 0, 0))
    painter = QPainter(full)
    window.render(painter, QPoint(0, 0), window.rect())
    painter.end()

    # 先保存完整图，方便定位
    full_path = os.path.join(os.path.dirname(__file__), "render_eye_idle_full4x.png")
    full.save(full_path)
    print(f"Saved full 4x -> {full_path} ({full.width()}x{full.height()})")

    # 裁剪出头/眼睛区域：根据实际 bbox (190,188,551,624) 估算
    crop = full.copy(QRect(230, 210, 290, 130))
    out_path = os.path.join(os.path.dirname(__file__), "render_eye_idle_zoom.png")
    crop.save(out_path)
    print(f"Saved zoomed eye -> {out_path}")


if __name__ == "__main__":
    main()
