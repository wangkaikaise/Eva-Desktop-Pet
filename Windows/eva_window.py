import os
import sys
import math
import time
import ctypes
import ctypes.wintypes as wintypes
import random
import logging
import traceback
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QApplication, QSystemTrayIcon, QMenu, QMessageBox, QWidget
)
from PySide6.QtCore import (
    Qt, QTimer, QPoint, QPointF, QSize, QRectF, QElapsedTimer,
    QThreadPool, QRunnable, QObject, Signal, QEvent, QAbstractNativeEventFilter
)
from PySide6.QtGui import (
    QPainter, QColor, QPixmap, QImage, QMouseEvent, QFont,
    QFontMetrics, QFontDatabase, QPainterPath, QLinearGradient,
    QRadialGradient, QAction, QIcon, QCursor, QPen
)
from PySide6.QtSvg import QSvgRenderer
from state_machine import PetStateMachine, PetAction, ACTION_TITLES, PetMood, Pose
from settings import (
    PetSettings, SettingsRepository, PetReminder, metrics_font_family,
)
from metrics import MetricsCollector
from reminders import ReminderScheduler

logger = logging.getLogger(__name__)

# Qt 6.8+ 才有 DevicePixelRatioChange 事件，低版本回退 None
_EVT_DPR_CHANGE = getattr(QEvent.Type, "DevicePixelRatioChange", None)
# 跨屏迁移后暂停重绘的窗口期（秒）：原生窗口/backing store 重建期间
# 继续高频重绘是跨屏崩溃（0xc0000409）的高危竞态
# 短暂跳过绘制即可让 backing store 完成重建；窗口位置仍持续 1:1 跟手。
_SCREEN_TRANSITION_GRACE = 0.16

# Windows 消息常量
_WM_DPICHANGED = 0x02E0
_WM_DISPLAYCHANGE = 0x011D
_WM_THEMECHANGED = 0x031A
_WM_SETTINGCHANGE = 0x001A


class WinDpiNativeFilter(QAbstractNativeEventFilter):
    """Windows 原生消息过滤器：在 Qt 处理 WM_DPICHANGED / WM_DISPLAYCHANGE
    **之前**设置跨屏保护标记。

    Qt 的 screenChanged 信号在 backing store 重建完成后才发出，此时崩溃
    可能已经发生。本过滤器在消息泵层面拦截，标记设置先于 Qt 的窗口表面
    重建，使随后的 paintEvent 能安全跳过。"""

    def __init__(self, on_screen_change, on_theme_change=None):
        super().__init__()
        self._callback = on_screen_change
        self._theme_callback = on_theme_change

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            try:
                msg_id = _extract_msg_id(message)
                if msg_id in (_WM_DPICHANGED, _WM_DISPLAYCHANGE):
                    self._callback()
                elif msg_id in (_WM_THEMECHANGED, _WM_SETTINGCHANGE) and self._theme_callback:
                    self._theme_callback()
            except Exception:
                pass
        return False  # 不阻断消息，让 Qt 正常处理


def _extract_msg_id(message):
    """从 PySide6 nativeEventFilter 的 message 参数中提取 Windows 消息 ID。
    PySide6 不同版本行为不一致：可能是 VoidPtr（地址）、int 或 bytes。"""
    # VoidPtr: shiboken6.Shiboken.VoidPtr — int() 取其内存地址
    try:
        addr = int(message)
        if addr > 0:
            # MSG 布局：HWND(8) + UINT message(4) + WPARAM(8) + LPARAM(8) + ...
            # message 字段在偏移 8 处（HWND 指针 8 字节后）
            msg = wintypes.MSG.from_address(addr)
            return msg.message
    except (TypeError, ValueError):
        pass
    if isinstance(message, (bytes, bytearray)):
        # bytes = 序列化的 MSG，message 字段在偏移 8 处（HWND=8 字节后）
        if len(message) >= 12:
            return int.from_bytes(message[8:12], "little")
    return 0


class MetricsWorkerSignals(QObject):
    result = Signal(dict)


class MetricsWorker(QRunnable):
    def __init__(self, metrics, signals):
        super().__init__()
        self.metrics = metrics
        # signals 由主窗口长期持有，避免 worker 被线程池删除后 queued 信号丢失
        self.signals = signals
        self.setAutoDelete(True)

    def run(self):
        try:
            data = self.metrics.sample()
            self.signals.result.emit(data)
        except Exception:
            logger.exception("MetricsWorker 采样失败")
            self.signals.result.emit({})


class NotificationBubble(QWidget):
    """独立通知对话框：圆角卡片风格，淡入淡出 + 自动消失。
    出现在宠物旁边，比托盘气泡更醒目，比模态弹窗更轻量。"""

    def __init__(self, title: str, message: str = "", anchor: QPoint = None,
                 accent: str = "#0A84FF", parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._title = title
        self._message = message or "伊娃提醒到啦~"
        self._accent = QColor(accent)
        self._anchor = anchor or QPoint(100, 100)
        self._alpha = 0.0
        self._elapsed = 0.0
        self._phase = "in"   # in → hold → out → done
        self._hold_duration = 6.0
        self._fade_duration = 0.4

        # 计算卡片大小
        font_title = QFont("Microsoft YaHei", 13)
        font_title.setBold(True)
        font_msg = QFont("Microsoft YaHei", 11)
        fm_t = QFontMetrics(font_title)
        fm_m = QFontMetrics(font_msg)
        tw = max(180, fm_t.horizontalAdvance(self._title) + 56)
        mw = max(180, fm_m.horizontalAdvance(self._message) + 56)
        self._card_w = max(tw, mw, 220)
        self._card_h = 84
        self.resize(self._card_w + 40, self._card_h + 40)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)  # ~60 FPS

    def _tick(self):
        dt = 0.016
        self._elapsed += dt
        if self._phase == "in":
            t = min(1.0, self._elapsed / self._fade_duration)
            self._alpha = t
            if t >= 1.0:
                self._phase = "hold"
                self._elapsed = 0.0
        elif self._phase == "hold":
            self._alpha = 1.0
            if self._elapsed >= self._hold_duration:
                self._phase = "out"
                self._elapsed = 0.0
        elif self._phase == "out":
            t = min(1.0, self._elapsed / self._fade_duration)
            self._alpha = 1.0 - t
            if t >= 1.0:
                self._phase = "done"
                self._timer.stop()
                self.close()
                self.deleteLater()
                return
        self.update()

    def show_at(self, pos: QPoint):
        """在指定位置附近显示通知。"""
        # 卡片中心对准 anchor，偏移到宠物上方
        cx = pos.x() - self._card_w // 2
        cy = pos.y() - self._card_h - 20  # 在宠物上方 20px
        # 确保不跑出屏幕：钳制到锚点所在的屏幕（双屏时不能强制拉回主屏）
        screen_geo = None
        try:
            for scr in QApplication.screens():
                if scr.geometry().contains(pos):
                    screen_geo = scr.availableGeometry()
                    break
        except Exception:
            pass
        if screen_geo is None:
            screen_geo = QApplication.primaryScreen().availableGeometry()
        cx = max(screen_geo.left() + 10, min(cx, screen_geo.right() - self._card_w - 10))
        cy = max(screen_geo.top() + 10, min(cy, screen_geo.bottom() - self._card_h - 10))
        self.move(cx, cy)
        self.show()
        self.raise_()
        self.activateWindow()

    def mousePressEvent(self, event: QMouseEvent):
        """点击通知即关闭。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._phase = "out"
            self._elapsed = 0.0

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        w, h = self.width(), self.height()
        # 卡片矩形（居中）
        cx, cy = w // 2, h // 2
        card_x = cx - self._card_w // 2
        card_y = cy - self._card_h // 2
        rect = QRectF(card_x, card_y, self._card_w, self._card_h)
        alpha = self._alpha

        # 阴影
        shadow = QColor(0, 0, 0, int(60 * alpha))
        p.setBrush(shadow)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect.adjusted(2, 4, 2, 4), 14, 14)

        # 卡片底
        p.setBrush(QColor(255, 255, 255, int(248 * alpha)))
        p.setPen(QColor(self._accent.red(), self._accent.green(), self._accent.blue(), int(120 * alpha)))
        p.drawRoundedRect(rect, 14, 14)

        # 左侧主题色条
        bar = QRectF(card_x + 1, card_y + 1, 5, self._card_h - 2)
        p.setBrush(QColor(self._accent.red(), self._accent.green(), self._accent.blue(), int(220 * alpha)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(bar, 2, 2)

        # 标题
        font_title = QFont("Microsoft YaHei", 13)
        font_title.setBold(True)
        p.setFont(font_title)
        p.setPen(QColor(28, 28, 30, int(255 * alpha)))
        p.drawText(
            QRectF(card_x + 20, card_y + 10, self._card_w - 40, 28),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._title,
        )

        # 消息
        font_msg = QFont("Microsoft YaHei", 11)
        p.setFont(font_msg)
        p.setPen(QColor(90, 90, 95, int(220 * alpha)))
        p.drawText(
            QRectF(card_x + 20, card_y + 40, self._card_w - 40, 28),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._message,
        )

        # 底部进度条（剩余时间）
        if self._phase == "hold":
            progress = 1.0 - (self._elapsed / self._hold_duration)
        else:
            progress = 1.0
        bar_y = card_y + self._card_h - 8
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, int(15 * alpha)))
        p.drawRoundedRect(QRectF(card_x + 20, bar_y, self._card_w - 40, 3), 1.5, 1.5)
        p.setBrush(QColor(self._accent.red(), self._accent.green(), self._accent.blue(), int(180 * alpha)))
        p.drawRoundedRect(QRectF(card_x + 20, bar_y, (self._card_w - 40) * progress, 3), 1.5, 1.5)


class EvaWindow(QMainWindow):
    def __init__(self, settings: PetSettings, repo: SettingsRepository):
        super().__init__()
        self.settings = settings
        self.repo = repo
        self.state = PetStateMachine(settings)
        try:
            self.state.mood = PetMood(settings.mood)
        except ValueError:
            self.state.mood = PetMood.CALM
        self.metrics = MetricsCollector(settings)
        self.reminders_list = repo.load_reminders()
        self.scheduler = ReminderScheduler(self.reminders_list, self._on_reminder)

        self._load_assets()
        self._init_window()
        self._init_timers()
        self._init_tray()
        self._apply_startup()

        self.dragging = False
        self.drag_start_mouse = QPoint()
        self.drag_start_window = QPoint()
        self._last_metrics_sample = {}
        self._metrics_counter = 0
        self._mouse_pos = QPoint()
        self._body_hit_path = QPainterPath()
        self.metrics_pool = QThreadPool.globalInstance()
        self._metrics_signals = MetricsWorkerSignals()  # 常驻信号对象，随窗口存活
        self._metrics_busy = False
        self._settings_dialog = None
        self._save_timer = QTimer(self)  # 磁盘写入防抖
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_settings)
        self._metrics_signals.result.connect(self._on_metrics_ready)

    def _load_assets(self):
        base = Path(__file__).parent / "assets"
        runtime_body = base / "character" / "eva-character-runtime-v15.png"
        source_body = base / "character" / "eva-character-source-transparent.png"
        # 发布包直接加载构建期生成的高清运行时纹理，避免首次启动逐像素处理。
        self.body_pixmap = QPixmap(str(runtime_body))
        if self.body_pixmap.isNull():
            self.body_pixmap = self._prepare_body_pixmap(str(source_body))
        self.light_pool_pixmap = QPixmap(str(base / "effects" / "glass-light-pool-640.png"))
        self.rocket_pixmap = self._render_svg_pixmap(
            base / "effects" / "eva-rocket.svg", QSize(1024, 1536)
        )
        self.app_icon = QIcon(str(base / "icons" / "eva-app.ico"))
        missing = []
        if self.body_pixmap.isNull():
            missing.append("character/eva-character-runtime-v15.png")
        if self.rocket_pixmap.isNull():
            missing.append("effects/eva-rocket.svg")
        if self.app_icon.isNull():
            missing.append("icons/eva-app.ico")
        if missing:
            raise FileNotFoundError("缺少或无法读取资源: " + ", ".join(missing))
        # 高分屏缓存：按物理像素 1:1 渲染身体贴图
        self._body_cache = None
        self._body_cache_key = None

    @staticmethod
    def _render_svg_pixmap(path: Path, size: QSize) -> QPixmap:
        """把矢量素材一次性栅格化到高分辨率缓存，避免运行时锯齿和重复解析。"""
        renderer = QSvgRenderer(str(path))
        if not renderer.isValid():
            return QPixmap()
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter)
        painter.end()
        return pixmap

    @staticmethod
    def _sharpen_mask_edges(pixmap: QPixmap) -> QPixmap:
        """让面罩（黑色脸）轮廓清晰且平滑（在任意分辨率输入上工作）：
        1. 构建黑色面罩 mask（亮度 < 100），孤立噪点（4 邻域暗点 < 2）过滤掉，
           避免头盔里的暗色噪点被放大成黑斑；
        2. 用圆形核膨胀（半径随分辨率缩放），吃掉边缘的灰色抗锯齿过渡带——
           圆形核避免方形核在对角方向产生的阶梯状“马赛克”；
        3. 膨胀结果经 1/2 降采样再放大（双线性），边界形成柔和过渡，
           即“清晰但不生硬”的抗锯齿边缘；
        4. 按柔和 alpha 把面罩黑 QColor(6,7,9) 混合回原图。
        处理范围限制在 y < 0.42H 的面罩区域，身体/裙子的暗色装饰不受影响。"""
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        W, H = image.width(), image.height()
        bytes_per_line = image.bytesPerLine()
        stride = bytes_per_line // 4
        # 膨胀半径随分辨率缩放：源图 512 宽时 K=3，更高分辨率按比例放大
        K = max(3, round(3 * W / 512))
        limit_y = int(H * 0.42)

        data = bytearray()
        for y in range(H):
            data.extend(image.scanLine(y))
        pixels = memoryview(data).cast("I")

        # Step 0: 上半区去青色。源图眼睛周围有大片青色光晕（g、b 明显高于 r），
        # 这些区域整体会被动态眼睛+遮罩替换，任何残留（尤其放大时）都是
        # “发绿+斑驳”瑕疵。上半区除眼睛光晕外没有其他青色元素，可安全去色：
        # 青色像素转为等效灰度（保持明暗层次），彻底消除色偏。
        for y in range(limit_y):
            base = y * stride
            for x in range(W):
                v = pixels[base + x]
                a = (v >> 24) & 0xFF
                if a < 100:
                    continue
                r = (v >> 16) & 0xFF
                g = (v >> 8) & 0xFF
                b = v & 0xFF
                if g - r > 18 and b - r > 18:
                    gray = (r * 30 + g * 59 + b * 11) // 100
                    pixels[base + x] = (a << 24) | (gray << 16) | (gray << 8) | gray

        # Step 1: 黑色面罩 mask + 孤立噪点过滤（至少 2 个 4 邻域暗点才算真面罩）
        dark = bytearray(H * stride)
        for y in range(limit_y):
            base = y * stride
            for x in range(W):
                v = pixels[base + x]
                a = (v >> 24) & 0xFF
                if a < 100:
                    continue
                r = (v >> 16) & 0xFF
                g = (v >> 8) & 0xFF
                b = v & 0xFF
                if (r + g + b) // 3 < 100:
                    dark[base + x] = 1
        mask = bytearray(len(dark))
        for y in range(limit_y):
            base = y * stride
            up = (y - 1) * stride if y > 0 else None
            dn = (y + 1) * stride if y < limit_y - 1 else None
            for x in range(W):
                if not dark[base + x]:
                    continue
                n = 0
                if x > 0 and dark[base + x - 1]:
                    n += 1
                if x < W - 1 and dark[base + x + 1]:
                    n += 1
                if up is not None and dark[up + x]:
                    n += 1
                if dn is not None and dark[dn + x]:
                    n += 1
                if n >= 2:
                    mask[base + x] = 1

        # Step 2: mask 转为白色不透明贴图（用于 Qt 图形管线膨胀）
        mdata = bytearray(W * H * 4)
        mview = memoryview(mdata).cast("I")
        for y in range(limit_y):
            base = y * stride
            for x in range(W):
                if mask[base + x]:
                    mview[y * stride + x] = 0xFFFFFFFF
        mask_pm = QPixmap.fromImage(
            QImage(mdata, W, H, bytes_per_line, QImage.Format.Format_ARGB32)
        )

        # Step 3: 圆形核膨胀 = 半径 K 圆盘内所有偏移的平移绘制取并集
        dil = QPixmap(W, H)
        dil.fill(Qt.GlobalColor.transparent)
        dp = QPainter(dil)
        for dx in range(-K, K + 1):
            for dy in range(-K, K + 1):
                if dx * dx + dy * dy <= K * K:
                    dp.drawPixmap(dx, dy, mask_pm)
        dp.end()

        # Step 4: 1/2 降采样再放大（双线性）→ 边界 1~2px 柔和渐变（抗锯齿）
        small = dil.scaled(
            max(1, W // 2), max(1, H // 2),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        soft = small.scaled(
            W, H,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        soft_img = soft.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        sbytes = bytearray()
        for y in range(H):
            sbytes.extend(soft_img.scanLine(y))
        spx = memoryview(sbytes).cast("I")

        # Step 5: 按柔和 alpha 把面罩黑混合回原图（255 直接覆盖，中间值线性插值）
        for y in range(limit_y):
            base = y * stride
            for x in range(W):
                sa = (spx[base + x] >> 24) & 0xFF
                if sa == 0:
                    continue
                if sa == 255:
                    pixels[base + x] = 0xFF060709
                else:
                    v = pixels[base + x]
                    a = (v >> 24) & 0xFF
                    r = (v >> 16) & 0xFF
                    g = (v >> 8) & 0xFF
                    b = v & 0xFF
                    f = sa / 255.0
                    nr = int(r * (1 - f) + 6 * f)
                    ng = int(g * (1 - f) + 7 * f)
                    nb = int(b * (1 - f) + 9 * f)
                    pixels[base + x] = (a << 24) | (nr << 16) | (ng << 8) | nb

        return QPixmap.fromImage(QImage(data, W, H, bytes_per_line, QImage.Format.Format_ARGB32))

    def _prepare_body_pixmap(self, path: str) -> QPixmap:
        """3× 超采样管线：所有处理在 1536×2304 高分辨率上执行。
        源图仅 512×768，最大尺寸+高分屏（如 size=520、DPR=2）需要 692×1040
        物理像素——直接用源图是“放大”，双线性插值必然模糊。
        超采样后任何显示尺寸都是“缩小”采样，物理上不可能糊。
        眼睛遮罩等矢量绘制在高分辨率上重新光栅化，边缘是原生抗锯齿的。"""
        src = Path(path)
        try:
            fingerprint = f"{src.stat().st_size}_{int(src.stat().st_mtime)}"
            cache_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "EvaDesktopPet" / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / f"body_macstyle_v15_{fingerprint}.png"
            if cache_path.exists():
                cached = QPixmap(str(cache_path))
                if not cached.isNull():
                    return cached
        except Exception:
            cache_path = None

        pixmap = QPixmap(path)
        if pixmap.isNull():
            return QPixmap(path)

        # Step 1: 3× 超采样——先用现有管线在源分辨率上清掉青色光晕/噪点，
        # 再放大到 1536×2304（灰阶内容双线性放大视觉无损），
        # 然后在高清尺度上重新执行边缘锐化（K 自动缩放到 9）。
        pixmap = self._sharpen_mask_edges(pixmap)  # 去青色 + 去噪点 + 基础锐化
        SS = 3
        big = pixmap.scaled(
            pixmap.width() * SS, pixmap.height() * SS,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # 高清尺度二次锐化：膨胀半径已按宽度缩放（K=9），把放大后的
        # 边缘过渡带重新压成高清抗锯齿边。
        pixmap = self._sharpen_mask_edges(big)

        W, H = pixmap.width(), pixmap.height()

        # 源图眼睛位置与 _draw_body 中 LEFT_EYE/RIGHT_EYE 对应；
        # 外扩足够大以压住源图眼部发光，但竖直方向不能过大，否则会覆盖面部下方。
        # 源图眼睛是横向椭圆（宽约 80-85px，高约 44px），遮罩做成宽扁椭圆更贴合。
        pad_x = W * 0.080
        pad_y = H * 0.030
        eye_w = W * 0.150 + pad_x * 2
        eye_h = H * 0.086 + pad_y * 2
        left_cx = W * 0.377 + W * 0.150 / 2
        right_cx = W * 0.597 + W * 0.150 / 2
        cy = H * 0.248 + H * 0.086 / 2

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        for cx in (left_cx, right_cx):
            rect = QRectF(cx - eye_w / 2, cy - eye_h / 2, eye_w, eye_h)
            grad = QRadialGradient(rect.center(), max(rect.width(), rect.height()) / 2)
            # 中心到 90% 纯黑，确保压住源图发光；最外层 10% 羽化过渡
            grad.setColorAt(0.0, QColor(6, 7, 9, 255))
            grad.setColorAt(0.90, QColor(6, 7, 9, 255))
            grad.setColorAt(1.0, QColor(6, 7, 9, 0))
            painter.setBrush(grad)
            painter.drawEllipse(rect)

        # 源图面罩右上角还有较强的环境反光，在动态眼睛上方形成"异常光点"，
        # 用一个较小的羽化椭圆局部压暗，保留面罩整体 3D 轮廓和边缘光泽。
        gx, gy, gw, gh = W * 0.773, H * 0.234, W * 0.120, H * 0.080
        rect = QRectF(gx - gw / 2, gy - gh / 2, gw, gh)
        grad = QRadialGradient(rect.center(), max(rect.width(), rect.height()) / 2)
        grad.setColorAt(0.0, QColor(6, 7, 9, 255))
        grad.setColorAt(0.90, QColor(6, 7, 9, 255))
        grad.setColorAt(1.0, QColor(6, 7, 9, 0))
        painter.setBrush(grad)
        painter.drawEllipse(rect)
        painter.end()

        if cache_path is not None:
            try:
                pixmap.save(str(cache_path), "PNG")
            except Exception:
                pass
        return pixmap

    def _init_window(self):
        self.setWindowTitle("伊娃桌面宠物")
        self.setWindowIcon(self.app_icon)
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self.settings.alwaysOnTop:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_MouseTracking, True)
        self.setMouseTracking(True)
        self.setWindowOpacity(self.settings.opacity)
        self.resize(self._window_width(), self._window_height())
        self._position_default()
        self.show()
        # 跨显示器迁移：窗口句柄创建后挂接屏幕切换信号
        # （Tool+无边框+半透明窗口跨屏时 Qt 会重建原生窗口，这里做防护）
        if self.windowHandle():
            self.windowHandle().screenChanged.connect(self._on_screen_changed)
        # 屏幕热插拔/主屏切换：窗口可能落在已不存在的屏幕区域，拉回可视区
        QApplication.instance().screenRemoved.connect(lambda _s: self._ensure_on_screen())
        # 跨屏迁移保护期时间戳（time.time()）
        self._screen_transition_until = 0.0
        # 安装 Windows 原生消息过滤器：在 Qt 处理 WM_DPICHANGED 之前
        # 设置保护标记，使 paintEvent 在 backing store 重建期间安全跳过
        self._native_filter = WinDpiNativeFilter(
            self._begin_screen_transition, self._on_theme_changed
        )
        try:
            QApplication.instance().installNativeEventFilter(self._native_filter)
        except Exception:
            logger.warning("无法安装原生事件过滤器")

    def _on_screen_changed(self, screen):
        """窗口迁移到另一块屏幕（含 DPI 变化）：使物理像素缓存失效，
        并暂停一小段时间的重绘，等原生窗口/backing store 重建完成。"""
        logger.info("窗口迁移到屏幕: %s", screen.name() if screen else "?")
        self._begin_screen_transition()

    def _begin_screen_transition(self):
        """跨屏迁移开始：停止动画定时器，使 backing store 重建期间
        不会产生任何 paint 请求。"""
        self._body_cache_key = None
        self._screen_transition_until = time.time() + _SCREEN_TRANSITION_GRACE
        # 停止动画定时器：定时器触发 self.update() → paintEvent，
        # 而 paintEvent 在 backing store 重建期间会原生崩溃
        try:
            self.anim_timer.stop()
        except Exception:
            pass
        # 延迟到保护期结束后恢复
        QTimer.singleShot(int(_SCREEN_TRANSITION_GRACE * 1000), self._after_screen_transition)

    def _after_screen_transition(self):
        """跨屏迁移完成后恢复：重算窗口逻辑尺寸、恢复定时器、请求一次重绘。"""
        try:
            if not self.isVisible():
                return
            if time.time() < self._screen_transition_until:
                return  # 期间又发生了一次迁移，等下一次定时器
            self.resize(self._window_width(), self._window_height())
            self._update_body_hit()
            # 恢复动画定时器
            if not self.anim_timer.isActive():
                self.anim_timer.start(50)
            self.update()
        except Exception:
            logger.exception("跨屏迁移恢复处理失败")

    def event(self, e):
        et = e.type()
        if et == QEvent.Type.ScreenChangeInternal or (
            _EVT_DPR_CHANGE is not None and et == _EVT_DPR_CHANGE
        ):
            self._begin_screen_transition()
        return super().event(e)

    def _init_timers(self):
        # 动画帧率：待机 20 FPS，活动 30 FPS
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._on_anim_frame)
        self.anim_timer.start(50)  # 20 FPS base; 活动时动态调整
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self._last_time = 0.0

        # 性能采样
        self.metrics_timer = QTimer(self)
        self.metrics_timer.timeout.connect(self._sample_metrics)
        if self.settings.metricsEnabled:
            self.metrics_timer.start(self.settings.metricsRefreshSeconds * 1000)
            # 自动启动温度助手（exe 以管理员权限运行时免 UAC）
            if self.settings.metricsShowCpuTemp:
                QTimer.singleShot(2000, self._auto_start_temp_helper)

        # 提醒
        self.reminder_timer = QTimer(self)
        self.reminder_timer.timeout.connect(self._check_reminders)
        self.reminder_timer.start(5000)

    def _init_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self._tray_icon())
        self.tray.setToolTip("伊娃桌面宠物")
        self.tray.setVisible(True)
        menu = QMenu()
        self.tray_menu = menu  # 持引用：setContextMenu 不接管所有权，否则可能被 GC 回收
        self.action_show = QAction("显示/隐藏伊娃", self)
        self.action_show.triggered.connect(self._toggle_visible)
        menu.addAction(self.action_show)
        menu.addSeparator()
        self.action_states = {}
        for action_enum in PetAction:
            a = QAction(ACTION_TITLES[action_enum], self)
            a.setCheckable(True)
            a.triggered.connect(lambda checked, ae=action_enum: self._set_action(ae))
            self.action_states[action_enum] = a
            menu.addAction(a)
        menu.addSeparator()
        self.action_settings = QAction("设置", self)
        self.action_settings.triggered.connect(self._open_settings)
        menu.addAction(self.action_settings)
        self.action_temp_admin = QAction("启用CPU温度（管理员）", self)
        self.action_temp_admin.triggered.connect(self._enable_cpu_temp_admin)
        menu.addAction(self.action_temp_admin)
        self.action_quit = QAction("退出", self)
        self.action_quit.triggered.connect(self._quit)
        menu.addAction(self.action_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)

    def _tray_icon(self):
        base = Path(__file__).parent / "assets" / "icons"
        icon_name = "eva-tray-white.ico"
        if os.name == "nt":
            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                ) as key:
                    light_theme, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
                icon_name = "eva-tray-black.ico" if light_theme else "eva-tray-white.ico"
            except Exception:
                pass
        return QIcon(str(base / icon_name))

    def _on_theme_changed(self):
        """系统主题切换后立即刷新黑白托盘头像。"""
        QTimer.singleShot(
            0,
            lambda: self.tray.setIcon(self._tray_icon())
            if hasattr(self, "tray") else None,
        )

    def _position_default(self):
        screen = QApplication.primaryScreen().availableGeometry()
        w = self.width()
        h = self.height()
        x = screen.right() - w - 24
        y = screen.bottom() - h - 42
        self.move(x, y)

    def _window_width(self):
        """窗口宽度：metrics 在左右时额外加宽，避免卡片遮挡本体。"""
        base = self.settings.size + 340
        if self.settings.metricsEnabled and self.settings.metricsPosition in ("left", "right"):
            font_scale = max(1.0, getattr(self.settings, "metricsFontSize", 10) / 10)
            return int(base + 240 * font_scale)
        return int(base)

    def _window_height(self):
        """窗口高度：metrics 在上下时额外加高，避免卡片遮挡本体。"""
        base = self.settings.size + 340
        if self.settings.metricsEnabled and self.settings.metricsPosition in ("top", "bottom"):
            font_scale = max(1.0, getattr(self.settings, "metricsFontSize", 10) / 10)
            return int(base + 180 * font_scale)
        return int(base)

    def _body_size(self):
        # 恢复源图原始比例，0.667 = 512/768，不再压缩高度
        h = int(self.settings.size * 1.0)
        w = int(h * (512 / 768))
        return w, h

    def _body_rect(self, pose):
        cx = self.width() // 2 + pose.x
        cy = self.height() // 2 + pose.y
        bw, bh = self._body_size()
        return QRectF(cx - bw / 2, cy - bh / 2, bw, bh)

    def _update_body_hit(self):
        # 根据当前位置更新命中路径（在 paintEvent 里调用）
        pose = self.state.get_current_pose()
        rect = self._body_rect(pose)
        self._body_hit_path = QPainterPath()
        self._body_hit_path.addEllipse(rect.adjusted(-10, -10, 10, 10))

    def _hit_test(self, pos: QPoint) -> bool:
        return self._body_hit_path.contains(pos)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._hit_test(event.pos()):
                self.dragging = True
                self.drag_start_mouse = event.globalPos()
                self.drag_start_window = self.frameGeometry().topLeft()
                self._drag_native_mouse = None
                self._drag_native_window = None
                if os.name == "nt":
                    point = wintypes.POINT()
                    rect = wintypes.RECT()
                    if (ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
                            and ctypes.windll.user32.GetWindowRect(
                                int(self.winId()), ctypes.byref(rect)
                            )):
                        self._drag_native_mouse = (point.x, point.y)
                        self._drag_native_window = (rect.left, rect.top)
                self.state.start_drag()
                self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
                event.accept()
            else:
                event.ignore()
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPos())
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        self._mouse_pos = event.position().toPoint()
        if self.dragging:
            try:
                native_delta = None
                if os.name == "nt" and self._drag_native_mouse is not None:
                    point = wintypes.POINT()
                    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
                    native_delta = (
                        point.x - self._drag_native_mouse[0],
                        point.y - self._drag_native_mouse[1],
                    )
                    new_pos = QPoint(
                        self._drag_native_window[0] + native_delta[0],
                        self._drag_native_window[1] + native_delta[1],
                    )
                else:
                    delta = event.globalPosition().toPoint() - self.drag_start_mouse
                    new_pos = self.drag_start_window + delta
                # ★ 主动检测屏幕跨越：在 move() 之前判断目标位置是否在
                # 不同屏幕上。如果是，提前设置保护标记，使 Qt 在处理
                # move → screen change → backing store 重建期间 paintEvent
                # 能安全跳过。
                try:
                    target_screen = QApplication.screenAt(new_pos)
                    current_screen = QApplication.screenAt(self.pos())
                    if (os.name != "nt" and target_screen and current_screen
                            and target_screen is not current_screen):
                        self._begin_screen_transition()
                except Exception:
                    pass
                if new_pos != self.pos():
                    self._move_window_1to1(new_pos)
            except Exception:
                logger.exception("拖动窗口失败（跨屏迁移中）")
            if native_delta is not None:
                dx, dy = native_delta
            else:
                logical_delta = event.globalPosition().toPoint() - self.drag_start_mouse
                dx, dy = logical_delta.x(), logical_delta.y()
            self.state.update_drag(dx, dy)
            event.accept()
        else:
            # 更新光标
            if self._hit_test(event.position().toPoint()):
                self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            else:
                self.unsetCursor()

    def _move_window_1to1(self, pos: QPoint):
        """直接把原生窗口放到鼠标增量位置，不做追赶插值。"""
        if os.name == "nt":
            hwnd = int(self.winId())
            flags = 0x0001 | 0x0004 | 0x0010  # NOSIZE | NOZORDER | NOACTIVATE
            if ctypes.windll.user32.SetWindowPos(
                hwnd, 0, int(pos.x()), int(pos.y()), 0, 0, flags
            ):
                return
        self.move(pos)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.dragging:
            self.dragging = False
            self.state.end_drag()
            self.unsetCursor()
            # 短点击检测：随机切状态
            if os.name == "nt" and self._drag_native_mouse is not None:
                point = wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
                moved = (
                    abs(point.x - self._drag_native_mouse[0])
                    + abs(point.y - self._drag_native_mouse[1])
                )
            else:
                moved = (event.globalPos() - self.drag_start_mouse).manhattanLength()
            if moved < 4:
                self.state.random_action()
                self._show_mood_message()
            event.accept()

    def _show_mood_message(self):
        msgs = {
            PetMood.CHEERFUL: ["今天也很棒呀", "你的好心情，我收到啦"],
            PetMood.CALM: ["慢一点也没关系", "陪你安静待一会儿"],
            PetMood.TIRED: ["累了就伸个懒腰吧", "先喝口水，再继续"],
            PetMood.FRUSTRATED: ["工作可以烦，别为难自己", "深呼吸，我陪着你"],
            PetMood.BLUE: ["今天不开心也没关系", "不用马上振作，我在这里"],
            PetMood.FOCUSED: ["专注模式，一起加油", "一步一步来就好"],
        }.get(self.state.mood, [])
        if msgs:
            self.state.show_message(random.choice(msgs))

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        for action_enum in PetAction:
            a = QAction(ACTION_TITLES[action_enum], self)
            a.triggered.connect(lambda checked, ae=action_enum: self._set_action(ae))
            a.setCheckable(True)
            a.setChecked(self.state.current_action == action_enum)
            menu.addAction(a)
        menu.addSeparator()
        a_settings = QAction("设置", self)
        a_settings.triggered.connect(self._open_settings)
        menu.addAction(a_settings)
        a_quit = QAction("退出", self)
        a_quit.triggered.connect(self._quit)
        menu.addAction(a_quit)
        menu.exec(pos)

    def _set_action(self, action: PetAction):
        if action == PetAction.SLEEP:
            self.state.set_action(action, 16.0)
        else:
            self.state.set_action(action, 12.0)

    def _toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_on_screen()

    def _ensure_on_screen(self):
        """多显示器断开/分辨率变化后，若窗口跑出所有屏幕范围则拉回主屏。"""
        geo = self.frameGeometry()
        visible = False
        for screen in QApplication.screens():
            if screen.availableGeometry().intersects(geo):
                visible = True
                break
        if not visible:
            self._position_default()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._toggle_visible()

    def _open_settings(self):
        """打开设置对话框。

        宠物主窗口是"不接受焦点"的置顶 Tool 窗口，若把对话框挂它下面，
        弹窗无法被系统激活、会被压在其他窗口后（表现为"打不开"）。
        因此对话框作为独立顶层窗口，并强制置顶 + 激活 + 屏幕居中。
        """
        try:
            from settings_dialog import SettingsDialog

            # 如果已经有一个设置对话框，确保它可见并置顶
            existing = getattr(self, "_settings_dialog", None)
            if isinstance(existing, SettingsDialog):
                if not existing.isVisible():
                    # 可能因某些原因被隐藏，重新居中并显示
                    existing.showNormal()
                    self._center_on_screen(existing)
                    existing.raise_()
                    existing.activateWindow()
                else:
                    existing.raise_()
                    existing.activateWindow()
                return

            dlg = SettingsDialog(self.settings, self.reminders_list, None,
                                 on_apply=self._on_settings_applied)
            # 作为独立 Dialog + 置顶窗口，避免被宠物窗口压住
            dlg.setWindowFlags(
                Qt.WindowType.Dialog
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.WindowCloseButtonHint
            )
            self._center_on_screen(dlg)
            self._settings_dialog = dlg
            dlg.finished.connect(self._on_settings_closed)
            dlg.show()
            # 稍微延迟再 raise/activate，确保窗口系统已分配句柄
            QTimer.singleShot(80, lambda: self._raise_dialog(dlg))
        except Exception:
            msg = f"设置界面打开失败：\n{traceback.format_exc()}"
            logger.error(msg)
            box = QMessageBox(QMessageBox.Icon.Warning, "伊娃", "设置界面打开失败，请重试。")
            box.setWindowFlags(
                box.windowFlags()
                | Qt.WindowType.WindowStaysOnTopHint
            )
            box.exec()

    def _raise_dialog(self, dlg):
        """强制把设置对话框带到最前并激活。"""
        if dlg and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()

    def _center_on_screen(self, widget):
        """把窗口放到主屏幕中央，避免出现在屏幕外或被其他窗口遮挡。"""
        try:
            screen = QApplication.primaryScreen().availableGeometry()
            geo = widget.frameGeometry()
            geo.moveCenter(screen.center())
            widget.move(geo.topLeft())
        except Exception:
            pass

    def _on_settings_closed(self):
        self._settings_dialog = None

    def _auto_start_temp_helper(self):
        """启动温度助手；普通权限下由 Windows 显示一次 UAC。
        首次使用时，提权助手会在自己的管理员上下文中安装 PawnIO。
        启动后 5 秒检查助手是否真正在产出数据，失败则显示诊断信息。
        """
        try:
            from metrics import (start_elevated_temp_helper, helper_status,
                                 write_temp_mode)
            # 写入当前温度模式
            temp_mode = getattr(self.settings, "metricsCpuTempMode", "max")
            write_temp_mode(temp_mode)
            status, val = helper_status()
            if status == "alive" and val:
                self.metrics.reset_temp_cache()
                self._sample_metrics()
                return
            ok, msg = start_elevated_temp_helper(temp_mode)
            if ok:
                logger.info("温度助手已启动（模式=%s）", temp_mode)
                QTimer.singleShot(7000, self._check_temp_helper_health)
            else:
                logger.warning("温度助手启动失败: %s", msg)
                self.tray.showMessage("CPU温度", msg, self.app_icon, 5000)
        except Exception:
            logger.error("自动启动温度助手失败:\n%s", traceback.format_exc())

    def _check_temp_helper_health(self):
        """启动后检查助手是否正常工作，失败时显示诊断信息。"""
        try:
            from metrics import helper_status, helper_error
            status, val = helper_status()
            if status == "alive" and val:
                self.metrics.reset_temp_cache()
                self._sample_metrics()
                return  # 助手正常工作
            # 助手没有产出数据，检查错误原因
            err = helper_error()
            if err:
                logger.error("温度助手启动失败，错误: %s", err)
                self.tray.showMessage(
                    "CPU温度",
                    f"温度助手启动失败：\n{err}\n\n"
                    f"可能原因：PawnIO 驱动未正确安装或\n"
                    f"被杀毒软件拦截。请尝试重启程序。",
                    self.app_icon, 8000)
            else:
                # 没有错误文件但也没数据：可能 .NET 加载失败或驱动缺失
                logger.warning("温度助手未产出数据且无错误文件")
                self.tray.showMessage(
                    "CPU温度",
                    "温度助手启动后未产出数据。\n"
                    "可能是 PawnIO 驱动未安装或被拦截。\n"
                    "请尝试在设置中手动启用 CPU 温度。",
                    self.app_icon, 8000)
        except Exception:
            pass

    def _enable_cpu_temp_admin(self):
        """通过 UAC 启动提权温度助手，恢复 CPU 温度显示。"""
        try:
            from metrics import start_elevated_temp_helper, helper_status

            # 已经在运行就直接刷新
            status, val = helper_status()
            if status == "alive" and val:
                self.metrics.reset_temp_cache()
                self.tray.showMessage(
                    "CPU温度",
                    f"温度助手已在运行，当前 CPU 温度约 {val:.0f} °C",
                    self.app_icon, 3000)
                return

            ret = QMessageBox.question(
                self, "启用CPU温度",
                "读取 CPU 硬件传感器需要内核驱动访问权限。\n\n"
                "将以管理员权限启动一个小型后台温度助手\n"
                "（首次使用会同时安装温度读取驱动）\n"
                "（宠物退出后它会自动结束），接下来会弹出\n"
                "UAC 确认框，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
            temp_mode = getattr(self.settings, "metricsCpuTempMode", "max")
            ok, msg = start_elevated_temp_helper(temp_mode)
            if not ok:
                box = QMessageBox(QMessageBox.Icon.Warning, "启用CPU温度",
                                  f"启动失败：{msg}")
                box.setWindowFlags(
                    box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
                box.exec()
                return

            def _check():
                try:
                    from metrics import helper_error
                    status, val = helper_status()
                    if status == "alive" and val:
                        self.metrics.reset_temp_cache()
                        self.tray.showMessage(
                            "CPU温度已启用",
                            f"当前 CPU 温度约 {val:.0f} °C",
                            self.app_icon, 4000)
                    else:
                        err = helper_error()
                        msg_text = "助手已启动，但暂时没有读到有效温度。\n"
                        if err:
                            msg_text += f"\n错误信息：{err}\n"
                        msg_text += "\n请稍候查看性能卡片；若持续无数据，\n可能是主板/驱动不支持。"
                        box = QMessageBox(
                            QMessageBox.Icon.Warning, "启用CPU温度",
                            msg_text)
                        box.setWindowFlags(
                            box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
                        box.exec()
                except Exception:
                    logger.error("检查温度助手结果失败:\n%s", traceback.format_exc())

            # 助手需要几秒完成 DLL 加载 + 首次采样
            QTimer.singleShot(8000, _check)
        except Exception:
            logger.error("启用CPU温度失败:\n%s", traceback.format_exc())

    def _on_settings_applied(self, settings, reminders):
        old = self.settings
        self.settings = settings
        self.state.settings = settings
        self.metrics.settings = settings
        self.reminders_list = reminders
        self.scheduler.rebuild(self.reminders_list)
        # 情绪设置同步进状态机（修复"设置不生效"）
        try:
            self.state.mood = PetMood(self.settings.mood)
        except ValueError:
            self.state.mood = PetMood.CALM
        self._apply_settings(old)
        # 磁盘写入防抖 400ms：拖滑块时不会每 tick 落盘
        self._save_timer.start(400)

    def _flush_settings(self):
        try:
            self.repo.save_settings(self.settings)
            self.repo.save_reminders(self.reminders_list)
        except Exception:
            logger.exception("保存设置/提醒失败")

    def _apply_settings(self, old=None):
        self.setWindowOpacity(self.settings.opacity)
        # 仅在置顶设置变化时重建窗口 flags（避免滑块拖动时反复重建窗口导致闪烁）
        if old is None or old.alwaysOnTop != self.settings.alwaysOnTop:
            flags = self.windowFlags()
            if self.settings.alwaysOnTop:
                flags |= Qt.WindowType.WindowStaysOnTopHint
            else:
                flags &= ~Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            self.show()
        # 尺寸：大小变化或性能卡片位置变化时重新计算窗口
        _metrics_changed = (
            old is None
            or old.metricsEnabled != self.settings.metricsEnabled
            or old.metricsPosition != self.settings.metricsPosition
            or old.metricsFont != self.settings.metricsFont
            or getattr(old, "metricsFontSize", 10) != self.settings.metricsFontSize
        )
        if old is None or old.size != self.settings.size or _metrics_changed:
            old_size = self.size()
            self.resize(self._window_width(), self._window_height())
            # 保持中心
            dx = (self.width() - old_size.width()) // 2
            dy = (self.height() - old_size.height()) // 2
            self.move(self.x() - dx, self.y() - dy)
        # 性能采样
        if self.settings.metricsEnabled:
            self.metrics_timer.start(self.settings.metricsRefreshSeconds * 1000)
            # 刚刚从关闭→开启时自动启动温度助手
            if (old is not None and self.settings.metricsShowCpuTemp
                    and (not old.metricsEnabled or not old.metricsShowCpuTemp)):
                QTimer.singleShot(1000, self._auto_start_temp_helper)
            # 温度模式变化时动态切换（写 mode 文件，助手下次循环读取）
            if old is not None and getattr(old, "metricsCpuTempMode", "max") != getattr(self.settings, "metricsCpuTempMode", "max"):
                try:
                    from metrics import write_temp_mode, helper_status
                    write_temp_mode(self.settings.metricsCpuTempMode)
                    self.metrics.reset_temp_cache()
                    logger.info("CPU 温度模式切换为: %s", self.settings.metricsCpuTempMode)
                    # 如果助手没在运行，重启它
                    h_status, _ = helper_status()
                    if h_status != "alive":
                        QTimer.singleShot(1000, self._auto_start_temp_helper)
                except Exception:
                    pass
        else:
            self.metrics_timer.stop()
        # 仅在自启设置变化时写注册表
        if old is None or old.startOnLogin != self.settings.startOnLogin:
            self._apply_startup()
        self.update()

    def _apply_startup(self):
        if os.name != "nt":
            return
        try:
            import winreg
            run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE
                )
            except FileNotFoundError:
                if not self.settings.startOnLogin:
                    return
                key = winreg.CreateKeyEx(
                    winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE
                )
            # 打包后 __file__ 指向解包临时目录，必须用 sys.executable
            if getattr(sys, "frozen", False):
                exe = sys.executable
            else:
                exe = os.path.abspath(sys.argv[0]) if sys.argv[0].endswith(".py") else os.path.abspath(__file__)
            if self.settings.startOnLogin:
                winreg.SetValueEx(key, "EvaDesktopPet", 0, winreg.REG_SZ, f'"{exe}"')
            else:
                try:
                    winreg.DeleteValue(key, "EvaDesktopPet")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception:
            logger.exception("开机自启设置失败")

    def _quit(self):
        # 先停动画/采样定时器，退出期间不再产生重绘（避免 teardown 竞态）
        for t in ("anim_timer", "metrics_timer", "reminder_timer"):
            try:
                timer = getattr(self, t, None)
                if timer is not None:
                    timer.stop()
            except Exception:
                pass
        # 清理线程池，避免退出时被在途的 nvidia-smi/PowerShell 子进程卡住数秒
        try:
            self.metrics_pool.clear()
            self.metrics_pool.waitForDone(300)
        except Exception:
            pass
        # 释放 LibreHardwareMonitor（CPU 温度）句柄
        try:
            from metrics import shutdown_lhm
            shutdown_lhm()
        except Exception:
            pass
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._flush_settings()
        self.tray.hide()
        QApplication.quit()

    def _on_reminder(self, reminder: PetReminder):
        # 独立通知对话框：出现在宠物旁边，比托盘气泡更醒目
        accent = self.state.current_accent()
        geo = self.frameGeometry()
        anchor = QPoint(geo.center().x(), geo.top() + int(self.settings.size * 0.15))
        bubble = NotificationBubble(
            title=reminder.title,
            message="伊娃提醒到啦~ 点击关闭",
            anchor=anchor,
            accent=accent,
        )
        # 持有强引用直到动画结束：Python GC 若在绘制期间回收 QWidget 包装
        # 会在原生层删掉正在绘制的窗口，是潜在的硬崩溃源
        if not hasattr(self, "_active_bubbles"):
            self._active_bubbles = []
        # 顺手清理已完成动画的旧气泡引用
        self._active_bubbles = [b for b in self._active_bubbles if b.isVisible()]
        self._active_bubbles.append(bubble)
        bubble.show_at(anchor)
        # 同时保留托盘通知（在通知中心可见）
        try:
            self.tray.showMessage(reminder.title, "伊娃提醒到啦~", self.app_icon, 5000)
        except Exception:
            pass

    def _check_reminders(self):
        self.scheduler.update()

    def _sample_metrics(self):
        if self._metrics_busy:
            return
        self._metrics_busy = True
        worker = MetricsWorker(self.metrics, self._metrics_signals)
        self.metrics_pool.start(worker)

    def _on_metrics_ready(self, data):
        self._metrics_busy = False
        self._last_metrics_sample = data
        self._metrics_counter += 1
        # 跨屏保护期内不触发重绘
        if time.time() >= getattr(self, "_screen_transition_until", 0.0):
            self.update()

    def _on_anim_frame(self):
        # 跨屏保护期内完全跳过：不调 tick、不调 update_body_hit、不 update
        # backing store 重建期间任何 Qt 对象操作都可能触发原生崩溃
        if time.time() < getattr(self, "_screen_transition_until", 0.0):
            return
        now = self._elapsed.elapsed() / 1000.0
        dt = now - self._last_time
        self._last_time = now
        if dt < 0 or dt > 0.5:
            dt = 0.05
        self.state.tick(dt)
        self._update_body_hit()
        # 根据状态调整帧率：活动 30FPS，待机 20FPS，休眠降到 10FPS 省电
        if self.dragging or self.state.current_action in (PetAction.HOVER, PetAction.CHEER, PetAction.PLAY):
            interval = 33
        elif self.state.current_action == PetAction.SLEEP:
            interval = 100
        else:
            interval = 50
        if self.anim_timer.interval() != interval:
            self.anim_timer.start(interval)
        self.update()

    def paintEvent(self, event):
        # ★ 跨屏保护：backing store 重建期间创建 QPainter(self) 会导致
        # 原生层 0xc0000409 崩溃。在保护期内直接返回，不创建任何绘制对象。
        # 这是最关键的一道防线——所有其他防护都是为了避免走到这里。
        if time.time() < getattr(self, "_screen_transition_until", 0.0):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        pose = self.state.get_current_pose()
        body_rect = self._body_rect(pose)
        bw, bh = self._body_size()

        # 1. 防护罩
        if self.settings.shieldEnabled:
            self._draw_shield(painter, body_rect, pose)

        # 2. 尾迹
        self._draw_trails(painter, body_rect, pose)

        # 3. 底部光池
        self._draw_light_pool(painter, body_rect, pose)

        # 4. 角色身体；玩耍状态通过独立图层与白色小火箭平滑交叉淡化。
        self._draw_body(painter, body_rect, pose)

        # 5. 休眠提示
        if self.state.action_opacity(PetAction.SLEEP) > 0.55:
            self._draw_sleep_indicator(painter, body_rect)

        # 6. 性能卡片
        if self.settings.metricsEnabled:
            self._draw_metrics(painter, body_rect)

        # 7. 消息气泡
        if self.state.message:
            self._draw_message(painter, body_rect)

    def _draw_body(self, painter: QPainter, body_rect: QRectF, pose: "Pose"):
        """绘制当前形态。进入/退出玩耍时仅交叉淡化，不让机器人先横转再闪图。"""
        play_alpha = self.state.action_opacity(PetAction.PLAY)
        robot_alpha = 1.0 - play_alpha

        if robot_alpha > 0.001:
            if play_alpha > 0.0:
                robot_action = (
                    self.state.target_action
                    if self.state.current_action == PetAction.PLAY
                    else self.state.current_action
                )
                robot_pose = self.state.pose_for(robot_action)
                robot_rect = self._body_rect(robot_pose)
            else:
                robot_pose, robot_rect = pose, body_rect
            self._draw_robot_body(painter, robot_rect, robot_pose, robot_alpha, robot_action if play_alpha > 0.0 else None)

        if play_alpha > 0.001:
            rocket_pose = self.state.pose_for(PetAction.PLAY)
            rocket_rect = self._body_rect(rocket_pose)
            self._draw_rocket_trail(painter, rocket_rect, rocket_pose, play_alpha)
            self._draw_rocket(painter, rocket_rect, rocket_pose, play_alpha)

    def _draw_robot_body(self, painter: QPainter, body_rect: QRectF, pose: "Pose",
                         layer_alpha: float = 1.0, action_override: PetAction = None):
        painter.save()
        painter.translate(body_rect.center())
        painter.rotate(pose.rotation)
        painter.scale(pose.scale, pose.scale)
        painter.translate(-body_rect.center())

        # 身体图：按物理像素（DPR）缓存，4K/5K 高分屏上 1:1 渲染不发糊
        # 跨屏瞬间 DPR 可能短暂异常，钳制到安全范围防止缓存键计算崩坏
        dpr = self.devicePixelRatioF()
        if not dpr or dpr <= 0 or dpr > 4.0:
            dpr = 1.0
        pw = max(1, round(body_rect.width() * dpr))
        ph = max(1, round(body_rect.height() * dpr))
        cache_key = (pw, ph, round(dpr, 2))
        if self._body_cache_key != cache_key:
            try:
                cached = self.body_pixmap.scaled(
                    pw, ph,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                cached.setDevicePixelRatio(dpr)
                self._body_cache = cached
                self._body_cache_key = cache_key
            except Exception:
                # 缩放失败（跨屏迁移中）退回原始贴图，绝不让绘制路径抛异常
                logger.exception("身体贴图缓存构建失败，退回原图")
                self._body_cache = self.body_pixmap
                self._body_cache_key = None
        # 透明度
        painter.setOpacity(self.settings.opacity * layer_alpha)
        painter.drawPixmap(QPointF(body_rect.left(), body_rect.top()), self._body_cache)

        # ---- 头部独立变换：眼睛和面罩高光跟随头部运动 ----
        # 头部旋转中心：面罩中心位置
        head_cx = body_rect.center().x()
        head_cy = body_rect.top() + body_rect.height() * 0.25
        painter.save()
        painter.translate(head_cx + pose.head_x, head_cy + pose.head_y)
        painter.rotate(pose.head_rotation)
        painter.translate(-(head_cx + pose.head_x), -(head_cy + pose.head_y))

        # 面罩顶部高光：Mac  glossy 头盔感，左上角一道弧形反光带
        # 进一步弱化：源图面罩已有自然反光，叠加过亮会产生“异常光点”。
        visor_rect = QRectF(
            body_rect.left() + body_rect.width() * 0.18,
            body_rect.top() + body_rect.height() * 0.148,
            body_rect.width() * 0.64,
            body_rect.height() * 0.052,
        )
        # 主高光：径向渐变，从左上向右下柔和扩散，避免锐利轮廓像金属
        gloss = QRadialGradient(
            visor_rect.left() + visor_rect.width() * 0.30,
            visor_rect.center().y(),
            visor_rect.width() * 0.62,
        )
        # 高光 alpha 不再乘 opacity：外层 painter.setOpacity 已统一控制
        gloss.setColorAt(0.0, QColor(255, 255, 255, 10))
        gloss.setColorAt(0.45, QColor(255, 255, 255, 4))
        gloss.setColorAt(0.80, QColor(255, 255, 255, 1))
        gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(gloss)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(visor_rect)
        # 额外小亮核：模拟光源最强点（位于面罩左上 1/4 处，更集中更淡）
        highlight = QRectF(
            body_rect.left() + body_rect.width() * 0.26,
            body_rect.top() + body_rect.height() * 0.156,
            body_rect.width() * 0.08,
            body_rect.height() * 0.034,
        )
        hl_grad = QRadialGradient(highlight.center(), max(highlight.width(), highlight.height()) / 2)
        # 统一由外层 painter.setOpacity 控制，不再二次乘 opacity
        hl_grad.setColorAt(0.0, QColor(255, 255, 255, 7))
        hl_grad.setColorAt(0.5, QColor(255, 255, 255, 2))
        hl_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(hl_grad)
        painter.drawEllipse(highlight)
        # 底部柔和反光：避免只有顶部高光造成"金属头盔"的单边感，呈现立体有机光泽
        bot_reflect = QRectF(
            body_rect.left() + body_rect.width() * 0.34,
            body_rect.top() + body_rect.height() * 0.215,
            body_rect.width() * 0.32,
            body_rect.height() * 0.028,
        )
        br_grad = QRadialGradient(
            bot_reflect.center().x(),
            bot_reflect.center().y(),
            max(bot_reflect.width(), bot_reflect.height()) / 2,
        )
        br_grad.setColorAt(0.0, QColor(255, 255, 255, 3))
        br_grad.setColorAt(0.6, QColor(255, 255, 255, 1))
        br_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(br_grad)
        painter.drawEllipse(bot_reflect)

        # 动态眼睛：按 Mac 风格面罩眼位精确定位
        # 源图眼睛约位于 visor 横向 1/3 与 2/3 处，更靠近、更圆润
        LEFT_EYE = (0.377, 0.248, 0.150, 0.086)
        RIGHT_EYE = (0.597, 0.248, 0.150, 0.086)
        left_rect = QRectF(
            body_rect.left() + body_rect.width() * LEFT_EYE[0],
            body_rect.top() + body_rect.height() * LEFT_EYE[1],
            body_rect.width() * LEFT_EYE[2],
            body_rect.height() * LEFT_EYE[3],
        )
        right_rect = QRectF(
            body_rect.left() + body_rect.width() * RIGHT_EYE[0],
            body_rect.top() + body_rect.height() * RIGHT_EYE[1],
            body_rect.width() * RIGHT_EYE[2],
            body_rect.height() * RIGHT_EYE[3],
        )
        eye_name = action_override.value if action_override is not None else None
        accent = QColor(self.state.current_accent())
        self._draw_eyes(
            painter, left_rect, right_rect, eye_name, accent,
            self.settings.opacity * pose.eye_alpha * layer_alpha,
        )

        painter.restore()  # 结束头部变换

        # 胸部核心：锁定中轴，源图核心中心约 y=0.548；尺寸比 Mac 参考略小更精致
        core_d = max(4, int(self.settings.size * 0.032))
        core_x = body_rect.center().x()
        core_y = body_rect.top() + body_rect.height() * 0.548
        accent = QColor(self.state.current_accent())
        # 柔光阴影
        painter.setOpacity(0.28 * self.settings.opacity * layer_alpha)
        painter.setBrush(accent)
        painter.drawEllipse(QRectF(core_x - core_d * 0.9, core_y - core_d * 0.9, core_d * 1.8, core_d * 1.8))
        # 外环
        painter.setOpacity(0.65 * self.settings.opacity * layer_alpha)
        painter.setPen(QColor(accent))
        pen = painter.pen()
        pen.setWidth(max(1, int(core_d * 0.24)))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(core_x - core_d / 2, core_y - core_d / 2, core_d, core_d))
        # 白色中心
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.setOpacity(self.settings.opacity * layer_alpha)
        painter.drawEllipse(QRectF(core_x - core_d * 0.32, core_y - core_d * 0.32, core_d * 0.64, core_d * 0.64))

        painter.restore()

    def _draw_rocket(self, painter: QPainter, body_rect: QRectF, pose: "Pose", alpha: float):
        """玩耍形态：三色矢量小火箭，方向跟随轨迹或拖动速度。"""
        h = body_rect.height() * 0.90
        w = h * (2.0 / 3.0)
        rect = QRectF(
            body_rect.center().x() - w / 2,
            body_rect.center().y() - h / 2,
            w,
            h,
        )
        painter.save()
        painter.translate(rect.center())
        painter.rotate(pose.rotation)
        painter.scale(pose.scale, pose.scale)
        painter.translate(-rect.center())
        painter.setOpacity(self.settings.opacity * alpha)
        painter.drawPixmap(rect, self.rocket_pixmap, QRectF(self.rocket_pixmap.rect()))
        painter.restore()

    def _draw_rocket_trail(self, painter: QPainter, body_rect: QRectF, pose: "Pose", alpha: float):
        """火箭尾气位于运动方向反侧，并随自动轨迹或拖动方向连续旋转。"""
        angle = math.radians(pose.rotation - 90.0)
        forward = QPointF(math.cos(angle), math.sin(angle))
        h = body_rect.height() * 0.90
        emitter = body_rect.center() - forward * (h * 0.44)
        speed = math.hypot(self.state.drag_vx, self.state.drag_vy) if self.dragging else 8.0
        length = min(h * 0.75, h * (0.22 + speed * 0.018))
        tail = emitter - forward * length
        accent = QColor(PetStateMachine.accent_for(PetAction.PLAY))

        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        beam = QLinearGradient(emitter, tail)
        beam.setColorAt(0.0, QColor(255, 255, 255, int(190 * alpha)))
        beam.setColorAt(0.30, QColor(accent.red(), accent.green(), accent.blue(), int(135 * alpha)))
        beam.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        perp = QPointF(-forward.y(), forward.x())
        width = max(3.0, body_rect.width() * 0.045)
        path = QPainterPath()
        path.moveTo(emitter + perp * width)
        path.lineTo(emitter - perp * width)
        path.lineTo(tail - perp * 0.8)
        path.lineTo(tail + perp * 0.8)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(beam)
        painter.drawPath(path)
        painter.restore()

    def _draw_eyes(self, painter: QPainter, left_rect: QRectF, right_rect: QRectF,
                   eye_name: str, accent: QColor, opacity: float):
        """Mac 风格程序化眼睛：黑色光泽面罩上的青色微笑弧线，
        每种情绪通过弧度、粗细与轻微位移区分。"""
        painter.save()

        # 先以带羽化的深色遮罩覆盖源图眼区。
        # 源图眼睛带有白色反光点，运行时直接叠新眼睛会让这些白点透出来，
        # 形成“眼睛旁边异常亮光”。这里用 SourceOver + radial gradient 覆盖：
        # 中心纯黑不透明以彻底压住源图高光，边缘 alpha 淡出到 0，
        # 与周围面罩自然融合，避免 Source 硬切带来的生硬黑块/亮边。
        # 遮罩范围要足够大，确保完全盖住源图眼睛亮光区。
        # 源图眼睛弧线顶部/两侧可能超出眼位矩形，padding 取较大值；
        # 中心纯黑不透明，边缘径向羽化，避免生硬边界。
        mask_pad_x = left_rect.width() * 0.55
        mask_pad_y = left_rect.height() * 0.70
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        for rect in (left_rect, right_rect):
            mask_rect = QRectF(
                rect.x() - mask_pad_x,
                rect.y() - mask_pad_y,
                rect.width() + mask_pad_x * 2,
                rect.height() + mask_pad_y * 2,
            )
            grad = QRadialGradient(mask_rect.center(), max(mask_rect.width(), mask_rect.height()) / 2)
            # 中心到 85% 纯黑，确保压住源图发光；最外层 15% 羽化过渡
            grad.setColorAt(0.0, QColor(0, 0, 0, 255))
            grad.setColorAt(0.85, QColor(0, 0, 0, 255))
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setBrush(grad)
            painter.drawEllipse(mask_rect)

        # 眼睛不二次乘 painter 透明度：外层 painter.setOpacity 已用于身体贴图，
        # 这里恢复 1.0，由传入的 opacity（settings.opacity * eye_alpha）单独控制眼睛亮度。
        painter.setOpacity(1.0)

        # 非玩耍动作在同一张脸上交叉淡化眼睛，避免过渡末尾突然跳形。
        if eye_name is not None:
            eye_layers = [(PetAction(eye_name), 1.0)]
        else:
            eye_layers = [
                (action, alpha)
                for action, alpha in self.state.action_layers()
                if action != PetAction.PLAY and alpha > 0.001
            ]
        total = sum(alpha for _, alpha in eye_layers) or 1.0
        for action, alpha in eye_layers:
            self._draw_eye_shape(
                painter, left_rect, right_rect, action,
                opacity * (alpha / total),
            )

        painter.restore()

    def _draw_eye_shape(self, painter: QPainter, left_rect: QRectF, right_rect: QRectF,
                        action: PetAction, opacity: float):
        eye_name = action.value
        color = QColor(PetStateMachine.accent_for(action))

        # 所有弧线状态统一粗细，仅弧度和低饱和动作色区分情绪。
        if eye_name == "idle":
            # 待机：温柔弧线，轻微眨眼节奏
            blink = 1.0 if (self.state.time * 1.7) % 4.2 > 0.18 else 0.08
            self._draw_eye_mac_arc(painter, left_rect, color, opacity * blink, width=0.22, height=0.34, glow=1.0)
            self._draw_eye_mac_arc(painter, right_rect, color, opacity * blink, width=0.22, height=0.34, glow=1.0)
        elif eye_name == "hover":
            # 巡航：略平略专注的弧线
            self._draw_eye_mac_arc(painter, left_rect, color, opacity, width=0.22, height=0.25, glow=1.0)
            self._draw_eye_mac_arc(painter, right_rect, color, opacity, width=0.22, height=0.25, glow=1.0)
        elif eye_name == "cheer":
            # 开心：明亮青绿笑眼，带轻微上下弹跳
            bounce = 0.50 + 0.08 * math.sin(self.state.time * self.state.speed * 2.8)
            self._draw_eye_mac_arc(painter, left_rect, color, opacity, width=0.22, height=bounce, glow=1.10)
            self._draw_eye_mac_arc(painter, right_rect, color, opacity, width=0.22, height=bounce, glow=1.10)
        elif eye_name == "play":
            # 玩耍：圆润兴奋眼（小圆圈带高光）
            self._draw_eye_mac_circle(painter, left_rect, color, opacity, size=0.85)
            self._draw_eye_mac_circle(painter, right_rect, color, opacity, size=0.85)
        elif eye_name == "sleep":
            # 睡眠：闭合的下弧线（眼睑），柔和紫色，亮度适度保持清晰
            self._draw_eye_mac_arc(painter, left_rect, color, opacity * 0.65, width=0.22, height=0.36, curve_down=True, glow=0.75)
            self._draw_eye_mac_arc(painter, right_rect, color, opacity * 0.65, width=0.22, height=0.36, curve_down=True, glow=0.75)
            # 偶尔打呼：一个小 z 浮到右眼旁边
            if (self.state.time * 0.6) % 3.5 < 0.6:
                self._draw_sleep_zzz(painter, right_rect, color, opacity * 0.65)

    def _draw_eye_mac_arc(self, painter: QPainter, rect: QRectF, color: QColor, opacity: float,
                          width: float, height: float, glow: float = 1.0, curve_down: bool = False):
        """Mac 风格弧线眼：柔和外光 + 饱满主弧线。
        两层严格对齐同一条路径，避免细窄内芯层形成可见水平亮线。"""
        path = QPainterPath()
        pad = rect.width() * 0.10
        start = QPointF(rect.left() + pad, rect.center().y())
        end = QPointF(rect.right() - pad, rect.center().y())
        if curve_down:
            ctrl = QPointF(rect.center().x(), rect.bottom() - rect.height() * (0.5 - height))
        else:
            ctrl = QPointF(rect.center().x(), rect.top() + rect.height() * (0.5 - height))
        path.moveTo(start)
        path.quadTo(ctrl, end)
        pen_w = max(2.5, rect.height() * width)
        # 两层：柔光 → 主弧线。移除单独的“内芯”层，避免细窄提亮线
        # 在反锯齿下形成肉眼可见的水平亮线/割裂感。
        for w_mul, alpha_mul, c in [
            (1.5, 0.18 * glow, color),
            (1.0, 1.00 * glow, color),
        ]:
            pen = QPen(QColor(c.red(), c.green(), c.blue(), int(255 * min(1.0, opacity * alpha_mul))))
            pen.setWidthF(pen_w * w_mul)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawPath(path)

    def _draw_eye_mac_circle(self, painter: QPainter, rect: QRectF, color: QColor, opacity: float, size: float = 1.0):
        """Mac 风格圆眼：玩耍时的圆润兴奋眼，外发光 + 饱满主体 + 内芯提亮 + 高光点。"""
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
        # 白色高光点
        painter.setBrush(QColor(255, 255, 255, int(245 * opacity)))
        painter.drawEllipse(QRectF(cx - r * 0.35, cy - r * 0.55, r * 0.40, r * 0.40))

    def _draw_sleep_zzz(self, painter: QPainter, rect: QRectF, color: QColor, opacity: float):
        """睡眠时在右眼右下方画一个小 z，远离其他 UI 元素。"""
        painter.setPen(QColor(color.red(), color.green(), color.blue(), int(255 * opacity)))
        font = QFont("Microsoft YaHei", 11, QFont.Weight.Bold)
        painter.setFont(font)
        # 放在右眼右下方（靠近身体肩部位置），不会与上方的 metrics 卡片冲突
        zx = rect.right() - rect.width() * 0.10
        zy = rect.bottom() + rect.height() * 0.5
        painter.drawText(QPointF(zx, zy), "z")

    def _draw_light_pool(self, painter: QPainter, body_rect: QRectF, pose: "Pose"):
        """底部光圈：收敛到脚底一圈柔和青蓝光，不向上漫出。
        与 Mac 参考图一致，像一只小小的地面光垫。"""
        cx = body_rect.center().x()
        ground_y = body_rect.bottom() + 1  # 光圈贴着伊娃脚底
        b = self.settings.lightPoolBrightness * self.settings.opacity
        t = self.state.time * self.state.speed
        breath = 1 + math.sin(t * 0.22) * 0.05
        flicker = 0.94 + 0.06 * math.sin(t * 1.7) * math.sin(t * 0.61)

        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        painter.setPen(Qt.PenStyle.NoPen)

        # ---- 主光圈：小而柔和的横向椭圆 ----
        pool_w = self.settings.size * 0.52 * breath
        pool_h = pool_w * 0.28
        painter.save()
        painter.translate(cx, ground_y)
        painter.scale(1.0, pool_h / pool_w)
        grad = QRadialGradient(0, 0, pool_w)
        # 中心偏白，外围淡蓝：整体像一盏柔和的冰蓝光垫
        grad.setColorAt(0.00, QColor(255, 255, 255, int(165 * b * flicker)))
        grad.setColorAt(0.18, QColor(245, 252, 255, int(110 * b * flicker)))
        grad.setColorAt(0.40, QColor(210, 242, 255, int(72 * b)))
        grad.setColorAt(0.65, QColor(160, 225, 255, int(34 * b)))
        grad.setColorAt(0.90, QColor(120, 210, 255, int(10 * b)))
        grad.setColorAt(1.00, QColor(120, 210, 255, 0))
        painter.setBrush(grad)
        painter.drawEllipse(QRectF(-pool_w, -pool_w, pool_w * 2, pool_w * 2))
        painter.restore()

        # ---- 接触亮核：脚底中心最亮的点 ----
        contact_w = pool_w * 0.28
        contact_h = contact_w * 0.55
        painter.save()
        painter.translate(cx, ground_y - 1)
        painter.scale(1.0, contact_h / contact_w)
        cg = QRadialGradient(0, 0, contact_w)
        cg.setColorAt(0.00, QColor(255, 255, 255, int(200 * b * flicker)))
        cg.setColorAt(0.40, QColor(230, 247, 255, int(80 * b)))
        cg.setColorAt(1.00, QColor(230, 247, 255, 0))
        painter.setBrush(cg)
        painter.drawEllipse(QRectF(-contact_w, -contact_w, contact_w * 2, contact_w * 2))
        painter.restore()

        painter.restore()

    def _draw_trails(self, painter: QPainter, body_rect: QRectF, pose: "Pose"):
        if self.state.current_action == PetAction.HOVER:
            # 巡航尾迹：三条与移动方向相反
            cx = body_rect.center().x()
            cy = body_rect.center().y()
            dx = math.cos(self.state.time * self.state.speed * 0.16)
            accent = QColor(self.state.current_accent())
            for i in range(3):
                t = self.state.time * self.state.speed * 0.6 + i * 0.9
                length = 18 + 8 * math.sin(t)
                x = cx - dx * (35 + i * 14) - math.sin(t) * 6
                y = cy + math.sin(t * 0.7) * 6 + i * 4
                grad = QLinearGradient(x, y, x - length * dx, y)
                grad.setColorAt(0, QColor(accent.red(), accent.green(), accent.blue(), 80))
                grad.setColorAt(1, QColor(accent.red(), accent.green(), accent.blue(), 0))
                painter.setBrush(grad)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QRectF(x - 3, y - 2, length, 4))
        elif self.dragging:
            # 拖动：从底部发射器推进的尾迹效果——光从脚底向下喷射，
            # 模仿火箭推进器从底部光晕发力，而不是从背部推进。
            cx = body_rect.center().x()
            # 尾迹起点在身体底部（发射器位置），而非身体中心
            emitter_y = body_rect.bottom() + 2
            accent = QColor(self.state.current_accent())
            vx = self.state.drag_vx
            vy = self.state.drag_vy
            speed = math.hypot(vx, vy)
            if speed < 0.4:
                # 静止：底部发射器的柔和光环
                painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 50))
                painter.setPen(Qt.PenStyle.NoPen)
                r = body_rect.width() * 0.35
                grad = QRadialGradient(cx, emitter_y, r)
                grad.setColorAt(0, QColor(accent.red(), accent.green(), accent.blue(), 80))
                grad.setColorAt(1, QColor(accent.red(), accent.green(), accent.blue(), 0))
                painter.setBrush(grad)
                painter.drawEllipse(QRectF(cx - r, emitter_y - r, r * 2, r * 2))
                return
            # 归一化方向（尾迹与运动方向相反）
            nx = -vx / max(speed, 1.0)
            ny = -vy / max(speed, 1.0)
            # 尾迹长度随速度增加（被"推"出来的感觉）
            trail_len = min(200, 50 + speed * 7)
            n_particles = 10
            for i in range(n_particles):
                t = i / (n_particles - 1)
                dist = trail_len * t
                sz = max(3, 18 * (1 - t * 0.70))
                alpha = int(170 * (1 - t) ** 1.4)
                if alpha < 6:
                    continue
                # 粒子从底部发射器沿运动反方向散开
                px = cx + nx * dist
                py = emitter_y + ny * dist + t * 8  # 略向下沉
                grad = QRadialGradient(px, py, sz)
                grad.setColorAt(0.0, QColor(255, 255, 255, alpha))
                grad.setColorAt(0.35, QColor(accent.red(), accent.green(), accent.blue(), int(alpha * 0.7)))
                grad.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
                painter.setBrush(grad)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QRectF(px - sz, py - sz, sz * 2, sz * 2))
            # 底部推进光束：从发射器向运动反方向喷射
            tail_x = cx + nx * trail_len
            tail_y = emitter_y + ny * trail_len
            beam = QLinearGradient(QPointF(cx, emitter_y), QPointF(tail_x, tail_y))
            beam.setColorAt(0, QColor(255, 255, 255, 120))
            beam.setColorAt(0.3, QColor(accent.red(), accent.green(), accent.blue(), 80))
            beam.setColorAt(0.75, QColor(accent.red(), accent.green(), accent.blue(), 25))
            beam.setColorAt(1, QColor(accent.red(), accent.green(), accent.blue(), 0))
            painter.setBrush(beam)
            painter.setPen(Qt.PenStyle.NoPen)
            bw = 8
            perp_x = -ny
            perp_y = nx
            path = QPainterPath()
            path.moveTo(QPointF(cx + perp_x * bw, emitter_y + perp_y * bw))
            path.lineTo(QPointF(cx - perp_x * bw, emitter_y - perp_y * bw))
            path.lineTo(QPointF(tail_x - perp_x * 1.5, tail_y - perp_y * 1.5))
            path.lineTo(QPointF(tail_x + perp_x * 1.5, tail_y + perp_y * 1.5))
            path.closeSubpath()
            painter.drawPath(path)
            # 底部发射器亮点：标明推进力来源
            emit_grad = QRadialGradient(cx, emitter_y, body_rect.width() * 0.15)
            emit_grad.setColorAt(0, QColor(255, 255, 255, int(200 * min(1.0, speed / 15))))
            emit_grad.setColorAt(0.5, QColor(accent.red(), accent.green(), accent.blue(), int(100 * min(1.0, speed / 15))))
            emit_grad.setColorAt(1, QColor(accent.red(), accent.green(), accent.blue(), 0))
            painter.setBrush(emit_grad)
            painter.drawEllipse(QRectF(cx - body_rect.width() * 0.15, emitter_y - body_rect.width() * 0.15,
                                       body_rect.width() * 0.3, body_rect.width() * 0.3))

    def _draw_shield(self, painter: QPainter, body_rect: QRectF, pose: "Pose"):
        cx = body_rect.center().x()
        cy = body_rect.center().y()
        r = max(body_rect.width(), body_rect.height()) * 0.72
        style = self.settings.shieldStyle
        accent = QColor(self.state.current_accent())
        painter.setPen(Qt.PenStyle.NoPen)
        if style == "halo":
            pen_color = QColor(accent.red(), accent.green(), accent.blue(), 60)
            pen = painter.pen()
            pen.setColor(pen_color)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
            # 扫描弧
            t = self.state.time * self.state.speed
            painter.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), int(t * 100) % 5760, 900)
        elif style == "bubble":
            painter.setBrush(QColor(255, 255, 255, 12))
            painter.setPen(QColor(accent.red(), accent.green(), accent.blue(), 50))
            painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        elif style == "orbit":
            for i in range(3):
                t = self.state.time * self.state.speed * (0.5 + i * 0.2) + i * 2.1
                rr = r * (0.8 + i * 0.08)
                painter.setPen(QColor(accent.red(), accent.green(), accent.blue(), 40 + i * 20))
                painter.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))

    def _draw_sleep_indicator(self, painter: QPainter, body_rect: QRectF):
        cx = body_rect.center().x() + body_rect.width() * 0.28
        cy = body_rect.top() - 10
        # 月亮
        painter.setBrush(QColor("#FFFFFF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(cx - 8, cy - 8, 16, 16))
        painter.setBrush(QColor("#05090D"))
        painter.drawEllipse(QRectF(cx - 4, cy - 10, 14, 14))
        # zzz
        font = QFont("Microsoft YaHei", 10)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        sizes = [10, 12, 14]
        for i, s in enumerate(sizes):
            font.setPointSize(s)
            painter.setFont(font)
            painter.drawText(int(cx + 12 + i * 10), int(cy - 5 + i * 3), "Z")

    def _draw_metrics(self, painter: QPainter, body_rect: QRectF):
        data = self._last_metrics_sample

        def _fmt(v, unit):
            return f"{int(round(v))}{unit}" if v is not None else "不可用"

        cpu_items = []
        if self.settings.metricsShowCpu:
            cpu_items.append(("CPU 占用", _fmt(data.get("cpu"), "%")))
        if self.settings.metricsShowCpuTemp:
            cpu_items.append(("CPU 温度", _fmt(data.get("cpu_temp"), "°C")))
        gpu_items = []
        if self.settings.metricsShowGpu:
            gpu_items.append(("GPU 占用", _fmt(data.get("gpu"), "%")))
        if self.settings.metricsShowGpuTemp:
            gpu_items.append(("GPU 温度", _fmt(data.get("gpu_temp"), "°C")))

        columns = [c for c in (cpu_items, gpu_items) if c]
        if not columns:
            return
        rows = max(len(c) for c in columns)

        # 使用用户从 Windows 已安装字体列表中选择的真实字体与字号。
        font = self._metrics_font()
        font.setBold(True)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        fm = QFontMetrics(font)

        pad_x = 16   # 左右内边距
        pad_y = 12   # 上下内边距
        col_gap = 24  # 两列间距
        label_value_gap = 18  # 标签与数值的最小间距
        col_ws = []
        for col in columns:
            w = 0
            for label, value in col:
                w = max(w, fm.horizontalAdvance(label) + label_value_gap + fm.horizontalAdvance(value))
            col_ws.append(w)
        w_total = pad_x * 2 + sum(col_ws) + col_gap * (len(col_ws) - 1)
        line_h = fm.height() + 7  # 行距加大，避免拥挤
        h_total = pad_y * 2 + rows * line_h

        # 卡片位置（按设置），使用安全间距确保不遮挡本体
        gap = max(28, body_rect.width() * 0.18)  # 间距与身体宽度成比例
        pos = self.settings.metricsPosition
        if pos == "right":
            x = body_rect.right() + gap
            y = body_rect.center().y() - h_total / 2
        elif pos == "left":
            x = body_rect.left() - w_total - gap
            y = body_rect.center().y() - h_total / 2
        elif pos == "top":
            x = body_rect.center().x() - w_total / 2
            y = body_rect.top() - h_total - gap
        else:  # bottom
            x = body_rect.center().x() - w_total / 2
            y = body_rect.bottom() + gap
        x = max(4.0, min(x, self.width() - w_total - 4))
        y = max(4.0, min(y, self.height() - h_total - 4))
        rect = QRectF(x, y, w_total, h_total)

        bg_opacity = self.settings.metricsBackgroundOpacity
        if bg_opacity > 0:
            painter.setOpacity(1.0)
            # 毛玻璃底色：极淡的深色 + 径向渐变，让桌面壁纸隐约透出
            glass_grad = QRadialGradient(
                rect.center().x(), rect.top() + rect.height() * 0.35,
                max(rect.width(), rect.height()) * 0.85
            )
            glass_grad.setColorAt(0.0, QColor(255, 255, 255, int(28 * bg_opacity)))
            glass_grad.setColorAt(0.35, QColor(28, 32, 40, int(110 * bg_opacity)))
            glass_grad.setColorAt(0.85, QColor(18, 22, 28, int(165 * bg_opacity)))
            glass_grad.setColorAt(1.0, QColor(12, 15, 20, int(185 * bg_opacity)))
            painter.setBrush(glass_grad)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 14, 14)

            # 顶部边缘高光：模拟玻璃表面反光
            gloss_h = rect.height() * 0.42
            gloss_rect = QRectF(rect.left() + 1, rect.top() + 1, rect.width() - 2, gloss_h)
            top_gloss = QLinearGradient(gloss_rect.left(), gloss_rect.top(),
                                        gloss_rect.left(), gloss_rect.bottom())
            top_gloss.setColorAt(0.0, QColor(255, 255, 255, int(55 * bg_opacity)))
            top_gloss.setColorAt(0.45, QColor(255, 255, 255, int(16 * bg_opacity)))
            top_gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(top_gloss)
            painter.drawRoundedRect(gloss_rect, 13, 13)

            # 细白边框
            border_pen = QPen(QColor(255, 255, 255, int(48 * bg_opacity)))
            border_pen.setWidthF(1.0)
            painter.setPen(border_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 14, 14)

            # 底部暗边：增加玻璃厚度感
            shadow_pen = QPen(QColor(0, 0, 0, int(50 * bg_opacity)))
            shadow_pen.setWidthF(1.2)
            painter.setPen(shadow_pen)
            painter.drawRoundedRect(QRectF(rect.left() + 0.6, rect.top() + 0.6,
                                           rect.width() - 1.2, rect.height() - 1.2), 14, 14)

        text_color = {
            "white": QColor("#FFFFFF"), "blue": QColor("#8AD4F7"), "black": QColor("#111111"),
        }.get(self.settings.metricsTextColor, QColor("#FFFFFF"))
        label_color = QColor(175, 184, 196)
        text_alpha = self.settings.metricsContentOpacity

        painter.setOpacity(text_alpha)
        for ci, col in enumerate(columns):
            col_x = rect.left() + pad_x + sum(col_ws[:ci]) + col_gap * ci
            ty = rect.top() + pad_y + fm.ascent()
            for label, value in col:
                painter.setOpacity(0.72 * text_alpha)
                painter.setPen(label_color)
                painter.drawText(QPointF(col_x, ty), label)
                painter.setOpacity(text_alpha)
                painter.setPen(text_color)
                vx = col_x + col_ws[ci] - fm.horizontalAdvance(value)
                painter.drawText(QPointF(vx, ty), value)
                ty += line_h
        painter.setOpacity(1.0)

    def _metrics_font(self) -> QFont:
        """Resolve the configured family without silently collapsing choices."""
        requested = metrics_font_family(self.settings.metricsFont)
        installed = set(QFontDatabase.families())
        candidates = (
            requested,
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "Segoe UI",
            QFont().defaultFamily(),
        )
        family = next((name for name in candidates if name in installed), candidates[-1])
        return QFont(family, getattr(self.settings, "metricsFontSize", 10))

    def _metrics_rect(self, body_rect: QRectF, w_total: float, h_total: float) -> QRectF:
        """与 _draw_metrics 一致的逻辑，返回性能卡片的最终 rect。"""
        gap = max(28, body_rect.width() * 0.18)
        pos = self.settings.metricsPosition
        if pos == "right":
            x, y = body_rect.right() + gap, body_rect.center().y() - h_total / 2
        elif pos == "left":
            x, y = body_rect.left() - w_total - gap, body_rect.center().y() - h_total / 2
        elif pos == "top":
            x, y = body_rect.center().x() - w_total / 2, body_rect.top() - h_total - gap
        else:  # bottom
            x, y = body_rect.center().x() - w_total / 2, body_rect.bottom() + gap
        x = max(4.0, min(x, self.width() - w_total - 4))
        y = max(4.0, min(y, self.height() - h_total - 4))
        return QRectF(x, y, w_total, h_total)

    def _draw_message(self, painter: QPainter, body_rect: QRectF):
        msg = self.state.message
        if not msg:
            return
        # 淡入淡出
        t = self.state.message_timer
        dur = self.state.message_duration
        if t < 0.65:
            alpha = t / 0.65
        elif t > dur - 0.55:
            alpha = max(0.0, (dur - t) / 0.55)
        else:
            alpha = 1.0
        font = QFont("Microsoft YaHei", 10, QFont.Weight.DemiBold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        text_w = min(220, fm.horizontalAdvance(msg) + 24)
        lines = 1 + len(msg) // 14
        text_h = 18 + lines * 16
        text_w = min(220, max(80, fm.horizontalAdvance(msg) + 24))
        # 性能卡片位置（如已启用）—— 选一个不重叠的方向放气泡
        metrics_rect = None
        if self.settings.metricsEnabled:
            # 用 _draw_metrics 同样的尺寸估算
            n_cols = 0
            if self.settings.metricsShowCpu or self.settings.metricsShowCpuTemp:
                n_cols += 1
            if self.settings.metricsShowGpu or self.settings.metricsShowGpuTemp:
                n_cols += 1
            n_rows = 0
            if self.settings.metricsShowCpu:
                n_rows += 1
            if self.settings.metricsShowCpuTemp:
                n_rows += 1
            if self.settings.metricsShowGpu:
                n_rows += 1
            if self.settings.metricsShowGpuTemp:
                n_rows += 1
            if n_cols > 0 and n_rows > 0:
                pad_x, pad_y, col_gap = 16, 12, 24
                line_h = fm.height() + 7
                w_total = pad_x * 2 + n_cols * 70 + col_gap * (n_cols - 1)
                h_total = pad_y * 2 + n_rows * line_h
                metrics_rect = self._metrics_rect(body_rect, w_total, h_total)
        # 选方向：默认上方；若 metrics 在顶部则放下方（左右时放对侧）
        pos = self.settings.metricsPosition if self.settings.metricsEnabled else None
        gap = 18
        if pos == "top":
            anchor_dir = "below"  # metrics 在上 → 气泡在下
        elif pos == "bottom":
            anchor_dir = "above"
        elif pos == "left":
            anchor_dir = "right"
        elif pos == "right":
            anchor_dir = "left"
        else:
            anchor_dir = "above"
        cx = body_rect.center().x()
        if anchor_dir == "above":
            x = cx - text_w / 2
            y = body_rect.top() - text_h - gap
        elif anchor_dir == "below":
            x = cx - text_w / 2
            y = body_rect.bottom() + gap
        elif anchor_dir == "right":
            x = body_rect.right() + gap
            y = body_rect.center().y() - text_h / 2
        else:  # left
            x = body_rect.left() - text_w - gap
            y = body_rect.center().y() - text_h / 2
        # 边界夹紧
        x = max(4.0, min(x, self.width() - text_w - 4))
        y = max(4.0, min(y, self.height() - text_h - 4))
        rect = QRectF(x, y, text_w, text_h)
        # 进一步：若与 metrics 仍有重叠，做二次偏移
        if metrics_rect is not None and rect.intersects(metrics_rect):
            if anchor_dir in ("above", "below"):
                # 改放到左右侧
                if metrics_rect.center().x() < cx:
                    rect.moveRight(body_rect.right() - 4)
                else:
                    rect.moveLeft(body_rect.left() + 4)
            else:
                # 改放到上下
                rect.moveBottom(body_rect.bottom() - 8)
        # 圆角深色气泡 + 主题色细边
        accent = QColor(self.state.current_accent())
        painter.setBrush(QColor(30, 35, 45, int(220 * alpha)))
        pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), int(140 * alpha)))
        pen.setWidthF(1.2)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 12, 12)
        # 小尾巴指向身体
        tail_h = 8
        if anchor_dir in ("above", "below"):
            tx = rect.center().x()
            if anchor_dir == "above":
                tail_top = rect.bottom()
                tail_bot = rect.bottom() + tail_h
            else:
                tail_top = rect.top()
                tail_bot = rect.top() - tail_h
            tail = QPainterPath()
            tail.moveTo(tx - 6, tail_top)
            tail.lineTo(tx, tail_bot)
            tail.lineTo(tx + 6, tail_top)
            tail.closeSubpath()
            painter.setBrush(QColor(30, 35, 45, int(220 * alpha)))
            painter.setPen(pen)
            painter.drawPath(tail)
        # 文字
        painter.setPen(QColor(255, 255, 255, int(255 * alpha)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, msg)
