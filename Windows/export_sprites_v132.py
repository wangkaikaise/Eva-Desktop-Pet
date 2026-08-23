"""导出当前 v13.2 设计的完整切图集：全身、各状态、头像、头部、素体。"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(__file__))

import tempfile
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()  # 绕过缓存，强制走当前管线

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QPen, QBrush
from PySide6.QtCore import QRectF, Qt, QSize

from settings import PetSettings, SettingsRepository
from eva_window import EvaWindow


def body_bbox(pm: QImage, y_limit_frac=1.0):
    """非透明内容 bbox。"""
    W, H = pm.width(), pm.height()
    minx, maxx, miny, maxy = W, -1, H, -1
    lim = int(H * y_limit_frac)
    for y in range(0, lim, 2):
        for x in range(0, W, 2):
            if (pm.pixel(x, y) >> 24) & 0xFF > 40:
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
    return minx, miny, maxx, maxy


def save_scaled(pm: QPixmap, out: str, w: int):
    h = int(pm.height() * w / pm.width())
    pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
              Qt.TransformationMode.SmoothTransformation).save(out)


def main():
    app = QApplication([])
    settings = PetSettings()
    settings.metricsEnabled = False
    settings.shieldEnabled = False
    repo = SettingsRepository()
    win = EvaWindow(settings, repo)
    win.state.time = 3.0  # 无眨眼/无 zzz 的稳定帧

    out_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "伊娃切图-v13.2"))
    os.makedirs(out_dir, exist_ok=True)

    W, H = win.body_pixmap.width(), win.body_pixmap.height()   # 1536x2304

    LEFT_EYE = (0.377, 0.248, 0.150, 0.086)
    RIGHT_EYE = (0.597, 0.248, 0.150, 0.086)
    body_rect = QRectF(0, 0, W, H)
    accent = QColor(win.state.current_accent())

    from state_machine import PetAction
    states = [
        (PetAction.IDLE, "idle"), (PetAction.HOVER, "hover"),
        (PetAction.CHEER, "cheer"), (PetAction.PLAY, "play"),
        (PetAction.SLEEP, "sleep"),
    ]

    rendered = {}
    for action, name in states:
        win.state.set_action(action)
        win.state.time = 3.0
        pm = QPixmap(win.body_pixmap)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lr = QRectF(body_rect.left() + W * LEFT_EYE[0], body_rect.top() + H * LEFT_EYE[1],
                    W * LEFT_EYE[2], H * LEFT_EYE[3])
        rr = QRectF(body_rect.left() + W * RIGHT_EYE[0], body_rect.top() + H * RIGHT_EYE[1],
                    W * RIGHT_EYE[2], H * RIGHT_EYE[3])
        win._draw_eyes(p, lr, rr, name, accent, 1.0)
        p.end()
        rendered[name] = pm
        save_scaled(pm, os.path.join(out_dir, f"eva_action_{name}.png"), 512)
        save_scaled(pm, os.path.join(out_dir, f"eva_action_{name}_2x.png"), 1024)
        print(f"[ok] action_{name}")

    # 素体（无眼睛）
    win.body_pixmap.save(os.path.join(out_dir, "eva_body_only_3x.png"))
    save_scaled(win.body_pixmap, os.path.join(out_dir, "eva_body_only.png"), 512)
    print("[ok] body_only")

    # 全身标准图 = idle
    rendered["idle"].save(os.path.join(out_dir, "eva_full_body_3x.png"))
    save_scaled(rendered["idle"], os.path.join(out_dir, "eva_full_body.png"), 512)
    print("[ok] full_body")

    # ---- 头部 / 头像（从 idle 3x 图裁剪）----
    idle3x = rendered["idle"].toImage()
    hx0, hy0, hx1, hy1 = body_bbox(idle3x, y_limit_frac=0.46)  # 头部内容区
    # 头像：取头部往下带一点肩，扩成正方形
    head_cx = (hx0 + hx1) / 2
    top = hy0 - (hy1 - hy0) * 0.06
    side = (hy1 - top) * 1.30          # 竖向 1.3 倍构成方图（含肩）
    left = head_cx - side / 2
    sq = QRectF(left, top, side, side)
    avatar = QPixmap(int(side), int(side))
    avatar.fill(Qt.GlobalColor.transparent)
    ap = QPainter(avatar)
    ap.setRenderHint(QPainter.RenderHint.Antialiasing)
    ap.drawPixmap(QRectF(0, 0, side, side), rendered["idle"], sq)
    ap.end()
    save_scaled(avatar, os.path.join(out_dir, "eva_avatar_square_1024.png"), 1024)
    save_scaled(avatar, os.path.join(out_dir, "eva_avatar_square_512.png"), 512)

    # 圆形头像
    circ = QPixmap(1024, 1024)
    circ.fill(Qt.GlobalColor.transparent)
    cp = QPainter(circ)
    cp.setRenderHint(QPainter.RenderHint.Antialiasing)
    path_clip = __import__("PySide6.QtGui", fromlist=["QPainterPath"]).QPainterPath()
    path_clip.addEllipse(2, 2, 1020, 1020)
    cp.setClipPath(path_clip)
    cp.drawPixmap(0, 0, avatar.scaled(1024, 1024, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation))
    cp.end()
    circ.save(os.path.join(out_dir, "eva_avatar_circle_1024.png"))
    print("[ok] avatar square/circle")

    # 纯头部特写（含面罩+眼睛，方形留少量边距）
    hw = (hx1 - hx0) * 1.08
    hh = (hy1 - hy0) * 1.12
    hc = QRectF(head_cx - hw / 2, hy0 - (hy1 - hy0) * 0.06, hw, hh)
    head_img = QPixmap(int(hw), int(hh))
    head_img.fill(Qt.GlobalColor.transparent)
    hp = QPainter(head_img)
    hp.setRenderHint(QPainter.RenderHint.Antialiasing)
    hp.drawPixmap(QRectF(0, 0, hw, hh), rendered["idle"], hc)
    hp.end()
    head_img.save(os.path.join(out_dir, "eva_head_3x.png"))
    save_scaled(head_img, os.path.join(out_dir, "eva_head.png"), 512)
    print("[ok] head")

    print(f"\n全部导出到: {out_dir}")


if __name__ == "__main__":
    main()
