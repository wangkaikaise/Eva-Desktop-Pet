import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QColor, QPainter
from PySide6.QtCore import Qt, QSize, QPoint

from settings import PetSettings, SettingsRepository
from eva_window import EvaWindow


def render_state(window, action, time_val=3.0, bg_color=QColor(0, 0, 0)):
    """渲染指定动作到带背景的大图。"""
    window.state.set_action(action)
    window.state.time = time_val
    window.update()

    # 按窗口尺寸的 2.5 倍渲染，便于观察细节
    base_size = window.size()
    scale = 2.5
    target = QSize(int(base_size.width() * scale), int(base_size.height() * scale))

    pixmap = QPixmap(target)
    pixmap.fill(bg_color)
    painter = QPainter(pixmap)
    # 将窗口内容缩放到目标尺寸
    window.render(painter, QPoint(0, 0), window.rect())
    painter.end()
    return pixmap


def main():
    app = QApplication([])
    settings = PetSettings()
    settings.metricsEnabled = False  # 关闭 metrics 卡片，避免干扰眼睛观察
    settings.shieldEnabled = False
    repo = SettingsRepository()
    window = EvaWindow(settings, repo)

    from state_machine import PetAction
    actions = [
        (PetAction.IDLE, "idle"),
        (PetAction.HOVER, "hover"),
        (PetAction.CHEER, "cheer"),
        (PetAction.PLAY, "play"),
        (PetAction.SLEEP, "sleep"),
    ]

    out_dir = os.path.dirname(__file__)
    for action, name in actions:
        pixmap = render_state(window, action)
        out_path = os.path.join(out_dir, f"render_eye_{name}.png")
        pixmap.save(out_path)
        print(f"Saved {name} -> {out_path}")


if __name__ == "__main__":
    main()
