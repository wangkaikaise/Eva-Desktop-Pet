#!/usr/bin/env python3
"""
伊娃桌面宠物素材导出脚本
----------------------------
作用：
1. 从源图复制/裁剪出全身图、头部、身体、左右手、胸灯、底座光等 PNG 切片；
2. 将 eyes/*.svg 渲染为高清 PNG；
3. 生成 5 种动作（idle/hover/cheer/play/sleep）的完整预览图。

输出目录：eva_desktop_pet/assets/sprites/
"""
import sys
import math
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QRect, QSize
from PySide6.QtGui import (
    QImage, QPainter, QColor, QPixmap, QFont, QPen,
    QPainterPath, QRadialGradient, QLinearGradient
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).parent.resolve()
ASSETS = ROOT / "assets"
OUT = ASSETS / "sprites"
OUT.mkdir(parents=True, exist_ok=True)

SOURCE = ASSETS / "character" / "eva-character-source-transparent.png"
EYES_DIR = ASSETS / "eyes"

# 源图尺寸
SRC_W, SRC_H = 512, 768

# 身体部位切图区域（基于 512×768 源图，按像素）
REGIONS = {
    "head":      (80, 0, 352, 330),     # 头部（含面罩）
    "body":      (120, 270, 272, 450),  # 躯干
    "left_arm":  (0, 250, 130, 360),    # 左手
    "right_arm": (382, 250, 130, 360),  # 右手
    "chest":     (186, 380, 140, 150),  # 胸灯及周围身体
    "base":      (140, 640, 232, 128),  # 脚底光圈区域
}

# 眼睛在源图中的位置（相对 source 的归一化坐标）
LEFT_EYE  = (0.471 - 0.164 / 2, 0.254 - 0.028 / 2, 0.164, 0.028)
RIGHT_EYE = (0.731 - 0.123 / 2, 0.254 - 0.028 / 2, 0.123, 0.028)


def load_source() -> QImage:
    img = QImage(str(SOURCE))
    if img.isNull():
        raise RuntimeError(f"无法加载源图: {SOURCE}")
    return img.convertToFormat(QImage.Format.Format_ARGB32)


def save_image(img: QImage, name: str):
    path = OUT / name
    if not img.save(str(path), "PNG"):
        raise RuntimeError(f"保存失败: {path}")
    print(f"  {path.name}")


def crop_region(img: QImage, box: tuple) -> QImage:
    x, y, w, h = box
    return img.copy(x, y, w, h)


def render_svg_to_png(svg_path: Path, size: QSize) -> QImage:
    """将 SVG 渲染为指定尺寸的 ARGB PNG。"""
    renderer = QSvgRenderer(str(svg_path))
    img = QImage(size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return img


def draw_eye_mac_arc(painter: QPainter, rect: QRectF, color: QColor, opacity: float,
                     width: float, height: float, glow: float = 1.0, curve_down: bool = False):
    """Mac 风格弧线眼：柔和外光 + 饱满主弧线。
    两层严格对齐同一路径，避免细窄内芯层形成可见水平亮线。"""
    path = QPainterPath()
    pad = rect.width() * 0.12
    start = rect.left() + pad
    end = rect.right() - pad
    if curve_down:
        ctrl_y = rect.bottom() - rect.height() * (0.5 - height)
    else:
        ctrl_y = rect.top() + rect.height() * (0.5 - height)
    path.moveTo(start, rect.center().y())
    path.quadTo(rect.center().x(), ctrl_y, end, rect.center().y())
    pen_w = max(2.5, rect.height() * width)
    # 两层：柔光 → 主弧线。移除单独的内芯层，避免细窄提亮线形成水平亮线。
    for w_mul, alpha_mul, c in [
        (1.5, 0.18 * glow, color),
        (1.0, 1.00 * glow, color),
    ]:
        pen = QPen(QColor(c.red(), c.green(), c.blue(), int(255 * min(1.0, opacity * alpha_mul))))
        pen.setWidthF(pen_w * w_mul)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(path)


def draw_eye_mac_circle(painter: QPainter, rect: QRectF, color: QColor, opacity: float, size: float = 1.0):
    """Mac 风格圆眼：玩耍时的圆润兴奋眼，外发光 + 饱满主体 + 内芯提亮 + 高光。"""
    r = min(rect.width(), rect.height()) * 0.38 * size
    cx, cy = rect.center().x(), rect.center().y()
    painter.setPen(Qt.PenStyle.NoPen)
    for w_mul, alpha_mul in [(1.8, 0.20), (1.0, 1.0)]:
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), int(255 * opacity * alpha_mul)))
        painter.drawEllipse(QRectF(cx - r * w_mul, cy - r * w_mul, r * 2 * w_mul, r * 2 * w_mul))
    # 内芯提亮：主色向白色提亮 35%，区域更大（0.80 倍半径），避免明显的圈层割裂
    core = QColor(color)
    core.setRed(min(255, color.red() + int((255 - color.red()) * 0.35)))
    core.setGreen(min(255, color.green() + int((255 - color.green()) * 0.35)))
    core.setBlue(min(255, color.blue() + int((255 - color.blue()) * 0.35)))
    painter.setBrush(QColor(core.red(), core.green(), core.blue(), int(255 * opacity * 0.50)))
    painter.drawEllipse(QRectF(cx - r * 0.80, cy - r * 0.80, r * 1.60, r * 1.60))
    painter.setBrush(QColor(255, 255, 255, int(245 * opacity)))
    painter.drawEllipse(QRectF(cx - r * 0.35, cy - r * 0.55, r * 0.40, r * 0.40))


def draw_eyes_on_image(img: QImage, eye_name: str, accent: QColor):
    """在源图上绘制指定动作的眼睛。
    idle 直接使用源图已有眼睛；其他动作以 Source 模式擦除源图眼睛后重绘，避免叠影。"""
    if eye_name == "idle":
        return

    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    w, h = img.width(), img.height()
    left_rect = QRectF(
        w * LEFT_EYE[0], h * LEFT_EYE[1],
        w * LEFT_EYE[2], h * LEFT_EYE[3]
    )
    right_rect = QRectF(
        w * RIGHT_EYE[0], h * RIGHT_EYE[1],
        w * RIGHT_EYE[2], h * RIGHT_EYE[3]
    )

    # 用带羽化的深色遮罩覆盖原有眼睛像素，防止原眼透出造成重影。
    # 源图眼睛弧线顶部/两侧可能超出眼位矩形，padding 取较大值；
    # 中心纯黑不透明以彻底压住源图高光，边缘径向羽化，避免生硬边界。
    # 与 eva_window._draw_eyes 一致的归一化遮罩几何（实测眼位）
    body_x = left_rect.x() - (left_rect.width() / 0.164) * (0.471 - 0.164 / 2)
    body_y = left_rect.y() - (left_rect.height() / 0.028) * (0.254 - 0.028 / 2)
    body_w = left_rect.width() / 0.164
    body_h = left_rect.height() / 0.028
    for cx_n, eye_w_n, pad_n in ((0.471, 0.164, 0.062), (0.731, 0.123, 0.045)):
        mw_n = eye_w_n + pad_n * 2
        mh_n = 0.028 + 0.050 * 2
        mask_rect = QRectF(
            body_x + (cx_n - mw_n / 2) * body_w,
            body_y + (0.254 - mh_n / 2) * body_h,
            mw_n * body_w, mh_n * body_h)
        grad = QRadialGradient(mask_rect.center(), max(mask_rect.width(), mask_rect.height()) / 2)
        grad.setColorAt(0.0, QColor(6, 7, 9, 255))
        grad.setColorAt(0.90, QColor(6, 7, 9, 255))
        grad.setColorAt(1.0, QColor(6, 7, 9, 0))
        painter.setBrush(grad)
        painter.drawEllipse(mask_rect)

    # 重绘动作眼睛
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    # 与运行时 eva_window.py 一致的高饱和亮色
    color = QColor("#33E5FF") if eye_name != "sleep" else QColor("#B8A6FF")

    if eye_name == "hover":
        # 巡航：略平略专注的弧线（粗细与其他状态一致）
        draw_eye_mac_arc(painter, left_rect, color, 1.0, 0.20, 0.28, glow=1.0)
        draw_eye_mac_arc(painter, right_rect, color, 1.0, 0.20, 0.28, glow=1.0)
    elif eye_name == "cheer":
        # 开心：明亮青绿笑眼，弧度更大
        cheer_color = QColor("#7BF5C8")
        draw_eye_mac_arc(painter, left_rect, cheer_color, 1.0, 0.20, 0.48, glow=1.15)
        draw_eye_mac_arc(painter, right_rect, cheer_color, 1.0, 0.20, 0.48, glow=1.15)
    elif eye_name == "play":
        # 玩耍：圆润兴奋眼
        draw_eye_mac_circle(painter, left_rect, color, 1.0, size=0.85)
        draw_eye_mac_circle(painter, right_rect, color, 1.0, size=0.85)
    elif eye_name == "sleep":
        # 睡眠：闭合的下弧线，柔和紫色，亮度适度保持清晰
        draw_eye_mac_arc(painter, left_rect, color, 0.65, 0.20, 0.36, curve_down=True, glow=0.75)
        draw_eye_mac_arc(painter, right_rect, color, 0.65, 0.20, 0.36, curve_down=True, glow=0.75)

    painter.end()


def main():
    app = QApplication(sys.argv)
    source = load_source()
    print(f"加载源图: {SOURCE} ({SRC_W}x{SRC_H})")

    # 1. 全身图
    print("\n[1/4] 全身图...")
    save_image(source.copy(), "eva_full_body.png")

    # 2. 身体部位切图
    print("\n[2/4] 身体部位切图...")
    for name, box in REGIONS.items():
        cropped = crop_region(source, box)
        save_image(cropped, f"eva_{name}.png")

    # 3. 眼睛 SVG → PNG
    print("\n[3/4] 眼睛 SVG 转 PNG...")
    eye_size = QSize(256, 256)
    for eye_name in ["idle", "hover", "cheer", "play", "sleep"]:
        svg = EYES_DIR / f"{eye_name}.svg"
        if svg.exists():
            eye_png = render_svg_to_png(svg, eye_size)
            save_image(eye_png, f"eye_{eye_name}.png")
        else:
            print(f"  跳过缺失: {svg}")

    # 4. 动作完整预览图
    print("\n[4/4] 动作预览图...")
    action_accents = {
        "idle":  QColor("#5ED7F2"),
        "hover": QColor("#7EB8F2"),
        "cheer": QColor("#6EE7B7"),
        "play":  QColor("#29B6F6"),
        "sleep": QColor("#B39DDB"),
    }
    for action, accent in action_accents.items():
        action_img = source.copy()
        draw_eyes_on_image(action_img, action, accent)
        save_image(action_img, f"eva_action_{action}.png")

    print(f"\n全部完成，输出目录: {OUT}")


if __name__ == "__main__":
    main()
