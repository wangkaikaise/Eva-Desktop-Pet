import copy
import uuid

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit,
    QListWidget, QListWidgetItem, QWidget, QGroupBox,
    QMessageBox, QDialogButtonBox, QStackedWidget,
    QFrame, QAbstractItemView, QScrollArea, QFormLayout
)
from PySide6.QtCore import (
    Qt, QSize, Signal, Property,
    QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from settings import PetSettings, PetReminder
from state_machine import PetMood, MOOD_TITLES


# ---------------------------------------------------------------- iOS 拨动开关
class ToggleSwitch(QWidget):
    """iOS 风格拨动开关：带 180ms 滑块动画，绿色=开启、灰色=关闭。"""

    toggled = Signal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = bool(checked)
        self._pos = 1.0 if checked else 0.0   # 滑块位置 0(关)~1(开)
        self._anim = QPropertyAnimation(self, b"thumbPos", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ---- 动画属性
    def get_thumb_pos(self) -> float:
        return self._pos

    def set_thumb_pos(self, v: float):
        self._pos = max(0.0, min(1.0, float(v)))
        self.update()

    thumbPos = Property(float, get_thumb_pos, set_thumb_pos)

    # ---- 开关接口（与 QCheckBox 同名，方便外部代码复用）
    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()
        self.toggled.emit(checked)

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.setChecked(not self._checked)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        t = self._pos
        # 轨道：灰(#E9E9EB) → 苹果绿(#34C759) 插值
        off = QColor(233, 233, 235)
        on = QColor(52, 199, 89)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * t),
            int(off.green() + (on.green() - off.green()) * t),
            int(off.blue() + (on.blue() - off.blue()) * t),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(0, 0, w, h, h / 2, h / 2)
        # 滑块：白色圆，带一点投影感的描边
        d = h - 6
        x = 3 + (w - d - 6) * t
        p.setBrush(QColor(255, 255, 255))
        p.setPen(QPen(QColor(0, 0, 0, 28), 1))
        p.drawEllipse(int(x + 0.5), 3, d, d)


# ---------------------------------------------------------------- 尺寸预设卡片
class SizePresetCard(QWidget):
    """尺寸预设卡片：内嵌迷你伊娃剪影（按比例）+ 明确的像素值标注。"""

    clicked = Signal(int)

    def __init__(self, px: int, name: str, parent=None):
        super().__init__(parent)
        self.px = px
        self.name = name
        self._selected = False
        self.setFixedSize(92, 102)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def setSelected(self, selected: bool):
        if selected != self._selected:
            self._selected = selected
            self.update()

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit(self.px)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # 卡片底：未选=白底灰边，选中=淡蓝底+蓝边（在灰背景上一眼可见）
        if self._selected:
            p.setBrush(QColor(232, 241, 255))
            p.setPen(QPen(QColor(10, 132, 255), 2))
        else:
            p.setBrush(QColor(255, 255, 255))
            p.setPen(QPen(QColor(216, 216, 220), 1))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 12, 12)
        # 迷你剪影：圆形身体 + 双眼，直径按预设值等比缩放
        body_d = 14 + (self.px - 140) / (520 - 140) * 34   # 14~48
        cx, cy = w / 2, h / 2 - 10
        accent = QColor(255, 149, 68) if self._selected else QColor(170, 185, 205)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        p.drawEllipse(int(cx - body_d / 2), int(cy - body_d / 2), int(body_d), int(body_d))
        # 眼睛
        eye_r = max(1.5, body_d * 0.07)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(int(cx - body_d * 0.18 - eye_r), int(cy - eye_r), int(eye_r * 2), int(eye_r * 2))
        p.drawEllipse(int(cx + body_d * 0.18 - eye_r), int(cy - eye_r), int(eye_r * 2), int(eye_r * 2))
        # 文字：名称 + 像素值
        p.setPen(QColor(28, 28, 30) if self._selected else QColor(90, 90, 95))
        f = QFont("Microsoft YaHei")
        f.setPointSizeF(8.5)
        f.setBold(True)
        p.setFont(f)
        p.drawText(int(0), int(h - 34), int(w), 16,
                   Qt.AlignmentFlag.AlignCenter, f"{self.name}")
        f.setPointSizeF(8)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(10, 132, 255) if self._selected else QColor(140, 140, 145))
        p.drawText(int(0), int(h - 19), int(w), 14,
                   Qt.AlignmentFlag.AlignCenter, f"{self.px} px")


# ---------------------------------------------------------------- 设置主窗口
class SettingsDialog(QDialog):
    """Mac 风格侧边栏设置页。

    交互逻辑（明确三键语义）：
    - 应用：把当前改动立即生效，窗口不关闭，可继续调整
    - 确定：应用改动并关闭窗口
    - 取消：放弃未应用的改动，恢复到上次应用的状态
    提醒的增删改自带确认对话框，操作后立即生效。
    """

    def __init__(self, settings: PetSettings, reminders: list, parent=None, on_apply=None):
        super().__init__(parent)
        self.setWindowTitle("伊娃设置")
        self.resize(880, 640)
        self.settings = copy.deepcopy(settings)   # 工作草稿（未应用）
        self._applied = copy.deepcopy(settings)   # 上次已应用的快照
        self.reminders = list(reminders)
        self.on_apply = on_apply
        self._build_ui()
        self._load_values()

    # ---------------------------------------------------------------- UI 构建
    def _build_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #EAEEF5;
                font-family: "Microsoft YaHei";
            }
            QFrame#leftPanel {
                background-color: rgba(255, 255, 255, 200);
                border-right: 1px solid rgba(0, 0, 0, 25);
                border-top-right-radius: 0px;
            }
            QFrame#contentPanel {
                background-color: rgba(247, 248, 252, 230);
            }
            QListWidget#sidebar {
                background-color: transparent;
                border: none;
                outline: none;
                padding: 4px;
                font-size: 14px;
            }
            QListWidget#sidebar::item {
                color: #1C1C1E;
                border-radius: 8px;
                padding: 7px 10px;
                margin: 1px 0px;
                font-weight: 600;
            }
            QListWidget#sidebar::item:selected {
                background-color: rgba(10, 132, 255, 230);
                color: #FFFFFF;
                font-weight: 700;
            }
            QListWidget#sidebar::item:hover:!selected {
                background-color: rgba(0, 0, 0, 18);
            }
            /* 白色半透明圆角卡片，玻璃质感 */
            QGroupBox {
                font-size: 14px;
                font-weight: 700;
                color: #1C1C1E;
                border: 1px solid rgba(0, 0, 0, 20);
                border-radius: 14px;
                margin-top: 22px;
                padding: 6px 0px 8px 0px;
                background-color: rgba(255, 255, 255, 210);
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 16px;
                top: 0px;
                padding: 0 6px;
                color: #1C1C1E;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel { color: #1C1C1E; font-size: 14px; }
            QLabel#sectionTitle {
                font-size: 24px;
                font-weight: 800;
                color: #000000;
                background: transparent;
            }
            QLabel#pageDesc {
                color: #8A8A8E;
                font-size: 13px;
                background: transparent;
            }
            QLabel#statusLabel {
                color: #8A8A8E;
                font-size: 13px;
                background: transparent;
            }
            QLabel#statusLabel[dirty="true"] {
                color: #FF9500;
            }
            QLabel#rowTitle {
                font-size: 14px;
                font-weight: 600;
                color: #1C1C1E;
                background: transparent;
            }
            QLabel#rowDesc {
                font-size: 12px;
                color: #8A8A8E;
                background: transparent;
            }
            QLabel#valueTag {
                color: #6E87A8;
                font-weight: 600;
                font-size: 13px;
                background: transparent;
            }
            QLabel#switchState {
                font-size: 13px;
                color: #8A8A8E;
                background: transparent;
            }
            QLabel#switchState[on="true"] {
                color: #34C759;
                font-weight: 600;
            }
            QLabel#formLabel {
                font-size: 14px;
                font-weight: 600;
                color: #1C1C1E;
                background: transparent;
            }
            QPushButton {
                background-color: rgba(10, 132, 255, 230);
                color: white;
                border: none;
                border-radius: 10px;
                padding: 8px 22px;
                font-weight: 600;
                font-size: 14px;
            }
            QPushButton:hover { background-color: rgba(10, 118, 224, 240); }
            QPushButton:pressed { background-color: rgba(9, 104, 196, 240); }
            QPushButton#primary {
                background-color: rgba(10, 132, 255, 230);
                color: white;
            }
            QPushButton#primary:hover { background-color: rgba(10, 118, 224, 240); }
            QPushButton#primary:pressed { background-color: rgba(9, 104, 196, 240); }
            QPushButton#secondary {
                background-color: rgba(229, 229, 234, 200);
                color: #1C1C1E;
            }
            QPushButton#secondary:hover { background-color: rgba(216, 216, 220, 220); }
            QPushButton#secondary:pressed { background-color: rgba(199, 199, 204, 220); }
            /* 柔和配色的滑块：淡雾蓝 */
            QSlider::groove:horizontal {
                height: 5px;
                background: rgba(0, 0, 0, 25);
                border-radius: 2.5px;
            }
            QSlider::sub-page:horizontal {
                background: #8FB5E4;
                border-radius: 2.5px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 1px solid rgba(0, 0, 0, 30);
                width: 16px;
                height: 16px;
                margin: -6px 0;
                border-radius: 9px;
            }
            QSlider::handle:horizontal:hover {
                border: 1px solid #A8C4E4;
            }
            QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
                background-color: rgba(255, 255, 255, 180);
                border: 1px solid rgba(0, 0, 0, 25);
                border-radius: 8px;
                padding: 6px 10px;
                min-height: 24px;
                font-size: 14px;
                color: #1C1C1E;
            }
            QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
                border: 1px solid #8FB5E4;
            }
            QComboBox::drop-down { border: none; width: 24px; }
            /* 下拉弹出菜单：不透明白色背景（rgba 在顶级窗口上会渲染成黑色） */
            QComboBox QAbstractItemView {
                background-color: #FFFFFF;
                border: 1px solid rgba(0, 0, 0, 20);
                border-radius: 8px;
                outline: none;
                padding: 4px;
                color: #1C1C1E;
            }
            QComboBox QAbstractItemView::item {
                color: #1C1C1E;
                background-color: #FFFFFF;
                padding: 6px 10px;
                border-radius: 6px;
                min-height: 24px;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #E8F1FF;
                color: #0A84FF;
            }
            QComboBox QAbstractItemView::item:selected {
                background-color: #D1E9FF;
                color: #0A84FF;
                font-weight: 600;
            }
            QScrollArea { background: transparent; border: none; }
            QListWidget#reminderList {
                background-color: rgba(247, 247, 250, 200);
                border: 1px solid rgba(0, 0, 0, 18);
                border-radius: 10px;
                padding: 6px;
                font-size: 14px;
            }
            QListWidget#reminderList::item {
                border-radius: 6px;
                padding: 8px 10px;
                color: #1C1C1E;
            }
            QListWidget#reminderList::item:selected {
                background-color: rgba(10, 132, 255, 40);
                color: #0A84FF;
            }
            QFrame#divider {
                background-color: rgba(0, 0, 0, 18);
                max-height: 1px;
                border: none;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(0, 0, 0, 50);
                border-radius: 3px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(0, 0, 0, 80);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- 左侧边栏 ----
        left_panel = QFrame()
        left_panel.setObjectName("leftPanel")
        left_panel.setFixedWidth(176)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 16, 8, 12)
        left_layout.setSpacing(0)
        title = QLabel("设置")
        title.setObjectName("sectionTitle")
        left_layout.addWidget(title)
        left_layout.addSpacing(10)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.sidebar.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sidebar.setUniformItemSizes(True)
        self._categories = [
            ("通用", "⚙"),
            ("情绪", "☺"),
            ("防护罩", "🛡"),
            ("性能卡片", "📊"),
            ("提醒", "⏰"),
        ]
        for idx, (name, icon) in enumerate(self._categories):
            item = QListWidgetItem(f"  {icon}  {name}")
            item.setSizeHint(QSize(item.sizeHint().width(), 36))
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.sidebar.addItem(item)
        left_layout.addWidget(self.sidebar, 1)

        # ---- 右侧内容 ----
        right_panel = QFrame()
        right_panel.setObjectName("contentPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(24, 18, 24, 12)

        self.section_title = QLabel("通用")
        self.section_title.setObjectName("sectionTitle")
        right_layout.addWidget(self.section_title)
        self.page_desc = QLabel("")
        self.page_desc.setObjectName("pageDesc")
        self.page_desc.setWordWrap(True)
        right_layout.addWidget(self.page_desc)
        right_layout.addSpacing(6)

        self.stack = QStackedWidget()
        self._build_general_page()
        self._build_mood_page()
        self._build_shield_page()
        self._build_metrics_page()
        self._build_reminders_page()
        right_layout.addWidget(self.stack, 1)

        # ---- 底部：状态提示 + 确定 / 应用 / 取消 ----
        bottom = QHBoxLayout()
        self.status_label = QLabel("改动点击「应用」后生效")
        self.status_label.setObjectName("statusLabel")
        bottom.addWidget(self.status_label)
        bottom.addStretch()

        # 底部按钮：确定 / 取消 / 应用，使用普通 QPushButton 确保样式完全可控
        self.btn_ok = QPushButton("确定")
        self.btn_ok.setObjectName("primary")
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("secondary")
        self.btn_apply = QPushButton("应用")
        self.btn_apply.setObjectName("primary")
        self.btn_ok.clicked.connect(self._on_ok)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_apply.clicked.connect(self._on_apply)
        bottom.addWidget(self.btn_ok)
        bottom.addWidget(self.btn_cancel)
        bottom.addWidget(self.btn_apply)
        right_layout.addLayout(bottom)

        layout.addWidget(left_panel)
        layout.addWidget(right_panel, 1)

        self.sidebar.currentRowChanged.connect(self._on_category_changed)
        self.sidebar.setCurrentRow(0)

    # ---------------------------------------------------------------- 通用构件
    def _page_widget(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background-color: transparent;")
        return page

    def _scroll_page(self, inner: QWidget) -> QWidget:
        """内容过长时可滚动，避免小窗口下控件被挤压。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        return scroll

    def _group(self, parent_layout: QVBoxLayout, title: str) -> QVBoxLayout:
        box = QGroupBox(title)
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(0)
        parent_layout.addWidget(box)
        return v

    def _divider(self, v: QVBoxLayout):
        line = QFrame()
        line.setObjectName("divider")
        line.setFixedHeight(1)
        v.addWidget(line)

    def _switch_row(self, v: QVBoxLayout, switch: ToggleSwitch, title: str, desc: str = ""):
        """开关行：左侧标题+说明，右侧「已开启/已关闭」状态文字 + iOS 拨动开关。"""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        h = QHBoxLayout(row)
        h.setContentsMargins(16, 11, 16, 11)
        h.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("rowTitle")
        left.addWidget(t)
        if desc:
            d = QLabel(desc)
            d.setObjectName("rowDesc")
            d.setWordWrap(True)
            left.addWidget(d)
        h.addLayout(left, 1)
        state = QLabel("已开启" if switch.isChecked() else "已关闭")
        state.setObjectName("switchState")
        state.setMinimumWidth(50)
        state.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(state, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)

        def _sync(checked: bool):
            state.setText("已开启" if checked else "已关闭")
            state.setProperty("on", checked)
            state.style().unpolish(state)
            state.style().polish(state)

        switch.toggled.connect(_sync)
        switch.toggled.connect(lambda *args: self._mark_dirty())
        v.addWidget(row)

    def _form_row(self, v: QVBoxLayout, label: str, control: QWidget):
        """普通表单行：左侧标签，右侧控件。"""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        h = QHBoxLayout(row)
        h.setContentsMargins(16, 10, 16, 10)
        h.setSpacing(12)
        t = QLabel(label)
        t.setObjectName("formLabel")
        h.addWidget(t)
        h.addStretch(1)
        h.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addWidget(row)
        return row

    def _slider_widget(self, slider: QSlider, fmt) -> QWidget:
        """滑块 + 实时数值标签。fmt: value -> str"""
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        w.setFixedWidth(240)
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        tag = QLabel(fmt(slider.value()))
        tag.setObjectName("valueTag")
        tag.setMinimumWidth(44)
        slider.valueChanged.connect(lambda val: tag.setText(fmt(val)))
        h.addWidget(slider, 1)
        h.addWidget(tag, 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    def _size_row(self, v: QVBoxLayout):
        """尺寸预设卡片行 + 自定义微调。"""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        col = QVBoxLayout(row)
        col.setContentsMargins(16, 12, 16, 12)
        col.setSpacing(10)

        cards = QWidget()
        cards.setStyleSheet("background: transparent;")
        h = QHBoxLayout(cards)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        self._size_cards = []
        presets = [(160, "迷你"), (220, "小巧"), (280, "标准"), (360, "大号"), (460, "超大")]
        for px, name in presets:
            card = SizePresetCard(px, name)
            card.clicked.connect(self._on_size_card_clicked)
            h.addWidget(card)
            self._size_cards.append(card)
        col.addWidget(cards)

        self.spin_size = QSpinBox()
        self.spin_size.setRange(140, 520)
        self.spin_size.setSingleStep(10)
        self.spin_size.setSuffix(" px")
        self.spin_size.valueChanged.connect(self._sync_size_cards)
        self._form_row(col, "自定义尺寸", self.spin_size)
        v.addWidget(row)

    def _on_size_card_clicked(self, px: int):
        self.spin_size.setValue(px)

    def _sync_size_cards(self, value: int):
        for card in getattr(self, "_size_cards", []):
            card.setSelected(card.px == value)

    # ---------------------------------------------------------------- 各页面
    def _build_general_page(self):
        page = self._page_widget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)

        g1 = self._group(v, "伙伴尺寸")
        self._size_row(g1)

        g2 = self._group(v, "外观")
        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(55, 100)
        self._form_row(g2, "整体透明度", self._slider_widget(self.slider_opacity, lambda val: f"{val}%"))
        self._divider(g2)
        self.spin_speed = QDoubleSpinBox()
        self.spin_speed.setRange(0.4, 1.0)
        self.spin_speed.setSingleStep(0.05)
        self.spin_speed.setDecimals(2)
        self.spin_speed.setSuffix(" ×")
        self._form_row(g2, "动作速度", self.spin_speed)

        g3 = self._group(v, "窗口")
        self.chk_topmost = ToggleSwitch()
        self._switch_row(g3, self.chk_topmost,
                         "窗口置顶", "伊娃始终显示在其他窗口最前面")
        self._divider(g3)
        self.chk_startup = ToggleSwitch()
        self._switch_row(g3, self.chk_startup,
                         "开机自动启动", "登录 Windows 后伊娃自动出现")

        g4 = self._group(v, "灯效")
        self.slider_light = QSlider(Qt.Orientation.Horizontal)
        self.slider_light.setRange(10, 100)
        self._form_row(g4, "底部光晕亮度", self._slider_widget(self.slider_light, lambda val: f"{val}%"))

        v.addStretch()
        self.stack.addWidget(self._scroll_page(page))

    def _build_mood_page(self):
        page = self._page_widget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)

        g1 = self._group(v, "情绪状态")
        self.combo_mood = QComboBox()
        for m in PetMood:
            self.combo_mood.addItem(MOOD_TITLES[m], m.value)
        self._form_row(g1, "当前情绪", self.combo_mood)

        g2 = self._group(v, "自动切换")
        self.chk_mood_auto = ToggleSwitch()
        self._switch_row(g2, self.chk_mood_auto,
                         "自动切换情绪", "伊娃会定时随机换一种心情")
        self._divider(g2)
        self.combo_mood_interval = QComboBox()
        for val in [15, 30, 60]:
            self.combo_mood_interval.addItem(f"每 {val} 分钟", val)
        self._form_row(g2, "切换间隔", self.combo_mood_interval)

        v.addStretch()
        self.stack.addWidget(self._scroll_page(page))

    def _build_shield_page(self):
        page = self._page_widget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)

        g1 = self._group(v, "防护罩")
        self.chk_shield = ToggleSwitch()
        self._switch_row(g1, self.chk_shield,
                         "启用防护罩", "围绕伊娃的装饰性光环特效，纯视觉观赏")
        self._divider(g1)
        self.combo_shield_style = QComboBox()
        self.combo_shield_style.addItem("光环（旋转光弧）", "halo")
        self.combo_shield_style.addItem("气泡（半透明护罩）", "bubble")
        self.combo_shield_style.addItem("轨道（三重光环环绕）", "orbit")
        self._form_row(g1, "防护罩样式", self.combo_shield_style)

        v.addStretch()
        self.stack.addWidget(self._scroll_page(page))

    def _build_metrics_page(self):
        page = self._page_widget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)

        g1 = self._group(v, "显示")
        self.chk_metrics = ToggleSwitch()
        self._switch_row(g1, self.chk_metrics,
                         "显示性能卡片", "在伊娃旁边显示 CPU / GPU 的实时状态")
        self._divider(g1)
        self.combo_metrics_pos = QComboBox()
        self.combo_metrics_pos.addItem("上方", "top")
        self.combo_metrics_pos.addItem("下方", "bottom")
        self.combo_metrics_pos.addItem("左侧", "left")
        self.combo_metrics_pos.addItem("右侧", "right")
        self._form_row(g1, "卡片位置", self.combo_metrics_pos)
        self._divider(g1)
        self.combo_metrics_refresh = QComboBox()
        for val in [2, 5, 10]:
            self.combo_metrics_refresh.addItem(f"每 {val} 秒", val)
        self._form_row(g1, "刷新间隔", self.combo_metrics_refresh)

        g2 = self._group(v, "显示内容")
        self.chk_show_cpu = ToggleSwitch()
        self._switch_row(g2, self.chk_show_cpu, "CPU 使用率")
        self._divider(g2)
        self.chk_show_cpu_temp = ToggleSwitch()
        self._switch_row(g2, self.chk_show_cpu_temp, "CPU 温度")
        self.combo_cpu_temp_mode = QComboBox()
        self.combo_cpu_temp_mode.addItem("最高温度", "max")
        self.combo_cpu_temp_mode.addItem("平均温度", "avg")
        self._form_row(g2, "温度模式", self.combo_cpu_temp_mode)
        self._divider(g2)
        self.chk_show_gpu = ToggleSwitch()
        self._switch_row(g2, self.chk_show_gpu, "GPU 使用率")
        self._divider(g2)
        self.chk_show_gpu_temp = ToggleSwitch()
        self._switch_row(g2, self.chk_show_gpu_temp, "GPU 温度")

        g3 = self._group(v, "卡片外观")
        self.slider_card_bg = QSlider(Qt.Orientation.Horizontal)
        self.slider_card_bg.setRange(0, 75)
        self._form_row(g3, "背景不透明度", self._slider_widget(self.slider_card_bg, lambda val: f"{val}%"))
        self._divider(g3)
        self.slider_card_content = QSlider(Qt.Orientation.Horizontal)
        self.slider_card_content.setRange(25, 100)
        self._form_row(g3, "文字不透明度", self._slider_widget(self.slider_card_content, lambda val: f"{val}%"))
        self._divider(g3)
        self.combo_font = QComboBox()
        self.combo_font.addItem("圆润（微软雅黑）", "rounded")
        self.combo_font.addItem("系统（Segoe UI）", "system")
        self.combo_font.addItem("等宽（Consolas）", "monospace")
        self._form_row(g3, "字体", self.combo_font)
        self._divider(g3)
        self.combo_text_color = QComboBox()
        self.combo_text_color.addItem("白色", "white")
        self.combo_text_color.addItem("蓝色", "blue")
        self.combo_text_color.addItem("黑色", "black")
        self._form_row(g3, "文字颜色", self.combo_text_color)

        v.addStretch()
        self.stack.addWidget(self._scroll_page(page))

    def _build_reminders_page(self):
        page = self._page_widget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)

        box = QGroupBox("提醒列表")
        box.setStyleSheet("QGroupBox { font-size: 14px; }")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(14, 20, 14, 14)
        inner.setSpacing(10)
        hint = QLabel("添加 / 编辑 / 删除后立即生效")
        hint.setObjectName("rowDesc")
        inner.addWidget(hint)
        self.list_reminders = QListWidget()
        self.list_reminders.setObjectName("reminderList")
        inner.addWidget(self.list_reminders, 1)

        h = QHBoxLayout()
        h.setSpacing(10)
        self.btn_add_reminder = QPushButton("添加")
        self.btn_edit_reminder = QPushButton("编辑")
        self.btn_edit_reminder.setObjectName("secondary")
        self.btn_delete_reminder = QPushButton("删除")
        self.btn_delete_reminder.setObjectName("secondary")
        h.addWidget(self.btn_add_reminder)
        h.addWidget(self.btn_edit_reminder)
        h.addWidget(self.btn_delete_reminder)
        h.addStretch()
        inner.addLayout(h)
        v.addWidget(box)

        self.btn_add_reminder.clicked.connect(self._add_reminder)
        self.btn_edit_reminder.clicked.connect(self._edit_reminder)
        self.btn_delete_reminder.clicked.connect(self._delete_reminder)
        self.stack.addWidget(page)

    # ---------------------------------------------------------------- 页面切换
    _PAGE_DESCRIPTIONS = {
        "通用": "伙伴的尺寸、外观、窗口行为和底部灯效。",
        "情绪": "设置伊娃当前的心情，会影响她的表情和说的话。",
        "防护罩": "围绕伙伴的装饰性光环特效，纯视觉效果。",
        "性能卡片": "在伙伴旁边显示电脑 CPU / GPU 的实时状态。",
        "提醒": "按固定时间或间隔弹出提醒，操作后立即生效。",
    }

    def _on_category_changed(self, row):
        self.stack.setCurrentIndex(row)
        name = self._categories[row][0]
        self.section_title.setText(name)
        self.page_desc.setText(self._PAGE_DESCRIPTIONS.get(name, ""))

    # ---------------------------------------------------------------- 数据读写
    def _load_values(self):
        self._loading = True   # 初始化期间不标记"未应用改动"
        s = self.settings
        self.spin_size.setValue(s.size)
        self._sync_size_cards(s.size)
        self.slider_opacity.setValue(int(s.opacity * 100))
        self.spin_speed.setValue(s.animationSpeed)
        self.chk_topmost.setChecked(s.alwaysOnTop)
        self.chk_startup.setChecked(s.startOnLogin)
        self.slider_light.setValue(int(s.lightPoolBrightness * 100))
        self.combo_mood.setCurrentIndex(self.combo_mood.findData(s.mood))
        self.chk_mood_auto.setChecked(s.moodAutoSwitch)
        self.combo_mood_interval.setCurrentIndex(
            self.combo_mood_interval.findData(s.moodIntervalMinutes))
        self.chk_shield.setChecked(s.shieldEnabled)
        self.combo_shield_style.setCurrentIndex(self.combo_shield_style.findData(s.shieldStyle))
        self.chk_metrics.setChecked(s.metricsEnabled)
        self.combo_metrics_pos.setCurrentIndex(
            self.combo_metrics_pos.findData(s.metricsPosition))
        self.combo_metrics_refresh.setCurrentIndex(
            self.combo_metrics_refresh.findData(s.metricsRefreshSeconds))
        self.chk_show_cpu.setChecked(s.metricsShowCpu)
        self.chk_show_cpu_temp.setChecked(s.metricsShowCpuTemp)
        self.combo_cpu_temp_mode.setCurrentIndex(
            self.combo_cpu_temp_mode.findData(getattr(s, "metricsCpuTempMode", "max")))
        self.chk_show_gpu.setChecked(s.metricsShowGpu)
        self.chk_show_gpu_temp.setChecked(s.metricsShowGpuTemp)
        self.slider_card_bg.setValue(int(s.metricsBackgroundOpacity * 100))
        self.slider_card_content.setValue(int(s.metricsContentOpacity * 100))
        self.combo_font.setCurrentIndex(self.combo_font.findData(s.metricsFont))
        self.combo_text_color.setCurrentIndex(self.combo_text_color.findData(s.metricsTextColor))
        self._refresh_reminder_list()
        # 控件变化只标记"有未应用改动"，不直接生效
        # （开关的 toggled 已在 _switch_row 里连接 _mark_dirty）
        conns = [
            (self.spin_size, "valueChanged"),
            (self.slider_opacity, "valueChanged"),
            (self.spin_speed, "valueChanged"),
            (self.slider_light, "valueChanged"),
            (self.combo_mood, "currentIndexChanged"),
            (self.combo_mood_interval, "currentIndexChanged"),
            (self.combo_shield_style, "currentIndexChanged"),
            (self.combo_metrics_pos, "currentIndexChanged"),
            (self.combo_metrics_refresh, "currentIndexChanged"),
            (self.slider_card_bg, "valueChanged"),
            (self.slider_card_content, "valueChanged"),
            (self.combo_font, "currentIndexChanged"),
            (self.combo_text_color, "currentIndexChanged"),
        ]
        for widget, signal in conns:
            getattr(widget, signal).connect(lambda *args: self._mark_dirty())
        self._loading = False

    def _mark_dirty(self):
        if getattr(self, "_loading", False):
            return
        self.status_label.setText("有未应用的改动")
        self.status_label.setProperty("dirty", True)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _mark_clean(self):
        self.status_label.setText("所有改动已应用")
        self.status_label.setProperty("dirty", False)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _collect(self):
        """把控件当前值收进工作草稿 self.settings。"""
        s = self.settings
        s.size = self.spin_size.value()
        s.opacity = self.slider_opacity.value() / 100.0
        s.animationSpeed = self.spin_speed.value()
        s.alwaysOnTop = self.chk_topmost.isChecked()
        s.startOnLogin = self.chk_startup.isChecked()
        s.lightPoolBrightness = self.slider_light.value() / 100.0
        s.mood = self.combo_mood.currentData()
        s.moodAutoSwitch = self.chk_mood_auto.isChecked()
        s.moodIntervalMinutes = self.combo_mood_interval.currentData()
        s.shieldEnabled = self.chk_shield.isChecked()
        s.shieldStyle = self.combo_shield_style.currentData()
        s.metricsEnabled = self.chk_metrics.isChecked()
        s.metricsPosition = self.combo_metrics_pos.currentData()
        s.metricsRefreshSeconds = self.combo_metrics_refresh.currentData()
        s.metricsShowCpu = self.chk_show_cpu.isChecked()
        s.metricsShowCpuTemp = self.chk_show_cpu_temp.isChecked()
        s.metricsCpuTempMode = self.combo_cpu_temp_mode.currentData()
        s.metricsShowGpu = self.chk_show_gpu.isChecked()
        s.metricsShowGpuTemp = self.chk_show_gpu_temp.isChecked()
        s.metricsBackgroundOpacity = self.slider_card_bg.value() / 100.0
        s.metricsContentOpacity = self.slider_card_content.value() / 100.0
        s.metricsFont = self.combo_font.currentData()
        s.metricsTextColor = self.combo_text_color.currentData()
        s.clamp()

    # ---------------------------------------------------------------- 按钮逻辑
    def _on_apply(self):
        """应用：改动立即生效，窗口保持打开。"""
        self._collect()
        if self.on_apply:
            self.on_apply(copy.deepcopy(self.settings), self.reminders)
        self._applied = copy.deepcopy(self.settings)
        self._mark_clean()

    def _on_ok(self):
        """确定：应用并关闭。"""
        self._collect()
        if self.on_apply:
            self.on_apply(copy.deepcopy(self.settings), self.reminders)
        self._applied = copy.deepcopy(self.settings)
        super().accept()

    def _on_cancel(self):
        """取消：放弃未应用的改动，恢复到上次应用的状态。"""
        if self.on_apply:
            self.on_apply(copy.deepcopy(self._applied), self.reminders)
        self.reject()

    # ---------------------------------------------------------------- 提醒
    def _refresh_reminder_list(self):
        self.list_reminders.clear()
        for r in self.reminders:
            if r.schedule == "daily":
                text = f"{r.title} · 每天 {r.hour:02d}:{r.minute:02d}"
            else:
                text = f"{r.title} · 每 {r.intervalMinutes} 分钟"
            if not r.isEnabled:
                text += "（已停用）"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, r)
            self.list_reminders.addItem(item)

    def _apply_reminders_only(self):
        """提醒立即生效，但不把未应用的设置草稿一并带出去。"""
        if self.on_apply:
            self.on_apply(copy.deepcopy(self._applied), self.reminders)

    def _add_reminder(self):
        dlg = ReminderDialog(self)
        if dlg.exec():
            self.reminders.append(dlg.reminder)
            self._refresh_reminder_list()
            self._apply_reminders_only()

    def _edit_reminder(self):
        item = self.list_reminders.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先在列表中选择一条提醒。")
            return
        r = item.data(Qt.ItemDataRole.UserRole)
        dlg = ReminderDialog(self, r)
        if dlg.exec():
            idx = self.reminders.index(r)
            self.reminders[idx] = dlg.reminder
            self._refresh_reminder_list()
            self._apply_reminders_only()

    def _delete_reminder(self):
        item = self.list_reminders.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先在列表中选择一条提醒。")
            return
        r = item.data(Qt.ItemDataRole.UserRole)
        self.reminders.remove(r)
        self._refresh_reminder_list()
        self._apply_reminders_only()


class ReminderDialog(QDialog):
    def __init__(self, parent=None, reminder=None):
        super().__init__(parent)
        self.setWindowTitle("新建提醒" if reminder is None else "编辑提醒")
        self.reminder = copy.deepcopy(reminder) if reminder else PetReminder(id=str(uuid.uuid4()))
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #F2F2F7;
                font-family: "Microsoft YaHei";
            }
            QLabel { color: #1C1C1E; font-size: 13px; }
            QPushButton {
                background-color: #0A84FF; color: white; border: none;
                border-radius: 8px; padding: 7px 18px;
                font-weight: 600; font-size: 13px;
            }
            QPushButton:hover { background-color: #0A76E0; }
            QComboBox, QSpinBox, QLineEdit {
                background-color: #FFFFFF;
                border: 1px solid #D8D8DC;
                border-radius: 8px;
                padding: 5px 10px;
                min-height: 22px;
                font-size: 13px;
                color: #1C1C1E;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                background-color: #FFFFFF;
                border: 1px solid #D8D8DC;
                border-radius: 8px;
                outline: none;
                padding: 4px;
                color: #1C1C1E;
            }
            QComboBox QAbstractItemView::item {
                color: #1C1C1E;
                background-color: #FFFFFF;
                padding: 6px 10px;
                border-radius: 6px;
                min-height: 22px;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #E8F1FF;
                color: #0A84FF;
            }
            QComboBox QAbstractItemView::item:selected {
                background-color: #D1E9FF;
                color: #0A84FF;
                font-weight: 600;
            }
        """)
        layout = QFormLayout(self)
        layout.setSpacing(12)
        self.edit_title = QLineEdit()
        self.edit_title.setPlaceholderText("例如：起来喝口水")
        layout.addRow("提醒内容", self.edit_title)
        self.combo_schedule = QComboBox()
        self.combo_schedule.addItem("每天固定时间", "daily")
        self.combo_schedule.addItem("按间隔重复", "interval")
        layout.addRow("重复方式", self.combo_schedule)
        self.spin_hour = QSpinBox()
        self.spin_hour.setRange(0, 23)
        self.spin_hour.setSuffix(" 时")
        self.spin_minute = QSpinBox()
        self.spin_minute.setRange(0, 59)
        self.spin_minute.setSuffix(" 分")
        h = QHBoxLayout()
        h.addWidget(self.spin_hour)
        h.addWidget(self.spin_minute)
        layout.addRow("每日时间", h)
        self.combo_interval = QComboBox()
        for v in [15, 30, 45, 60, 90, 120]:
            self.combo_interval.addItem(f"每 {v} 分钟", v)
        layout.addRow("重复间隔", self.combo_interval)
        self.chk_enabled = ToggleSwitch()
        layout.addRow("启用该提醒", self.chk_enabled)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _load_values(self):
        r = self.reminder
        self.edit_title.setText(r.title)
        self.combo_schedule.setCurrentIndex(self.combo_schedule.findData(r.schedule))
        self.spin_hour.setValue(r.hour)
        self.spin_minute.setValue(r.minute)
        self.combo_interval.setCurrentIndex(self.combo_interval.findData(r.intervalMinutes))
        self.chk_enabled.setChecked(r.isEnabled)

    def accept(self):
        r = self.reminder
        r.title = self.edit_title.text().strip() or "提醒"
        r.schedule = self.combo_schedule.currentData()
        r.hour = self.spin_hour.value()
        r.minute = self.spin_minute.value()
        r.intervalMinutes = self.combo_interval.currentData()
        r.isEnabled = self.chk_enabled.isChecked()
        super().accept()
