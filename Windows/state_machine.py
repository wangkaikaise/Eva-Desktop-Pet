import random
import math
from enum import Enum
from dataclasses import dataclass


class PetAction(Enum):
    IDLE = "idle"
    HOVER = "hover"
    CHEER = "cheer"
    PLAY = "play"
    SLEEP = "sleep"


ACTION_TITLES = {
    PetAction.IDLE: "待机",
    PetAction.HOVER: "巡航",
    PetAction.CHEER: "开心",
    PetAction.PLAY: "玩耍",
    PetAction.SLEEP: "休眠",
}


class PetMood(Enum):
    CHEERFUL = "cheerful"
    CALM = "calm"
    TIRED = "tired"
    FRUSTRATED = "frustrated"
    BLUE = "blue"
    FOCUSED = "focused"


MOOD_TITLES = {
    PetMood.CHEERFUL: "开心营业",
    PetMood.CALM: "平静摸鱼",
    PetMood.TIRED: "有点疲惫",
    PetMood.FRUSTRATED: "工作烦躁",
    PetMood.BLUE: "今日郁闷",
    PetMood.FOCUSED: "专注奋斗",
}


MOOD_MESSAGES = {
    PetMood.CHEERFUL: ["今天也很棒呀", "你的好心情，我收到啦"],
    PetMood.CALM: ["慢一点也没关系", "陪你安静待一会儿"],
    PetMood.TIRED: ["累了就伸个懒腰吧", "先喝口水，再继续"],
    PetMood.FRUSTRATED: ["工作可以烦，别为难自己", "深呼吸，我陪着你"],
    PetMood.BLUE: ["今天不开心也没关系", "不用马上振作，我在这里"],
    PetMood.FOCUSED: ["专注模式，一起加油", "一步一步来就好"],
}


@dataclass
class Pose:
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scale: float = 1.0
    eye_alpha: float = 1.0
    head_x: float = 0.0  # 头部相对于身体的独立水平偏移
    head_y: float = 0.0  # 头部相对于身体的独立垂直偏移
    head_rotation: float = 0.0  # 头部相对于身体的独立旋转


class PetStateMachine:
    def __init__(self, settings):
        self.settings = settings
        self.current_action = PetAction.IDLE
        self.target_action = PetAction.IDLE
        self.transition_progress = 1.0
        self.transition_duration = 1.8
        self.action_timer = 0.0
        self.action_duration = 0.0
        self.dragging = False
        self.drag_dir = None  # 'left','right','up','down'
        self.drag_offset_x = 0.0
        self.drag_offset_y = 0.0
        self.drag_release_time = -10.0
        # 拖拽物理：速度（像素/事件，平滑后）与松手回弹
        self.drag_vx = 0.0
        self.drag_vy = 0.0
        self._last_drag_dx = 0.0
        self._last_drag_dy = 0.0
        self._release_vx = 0.0
        self._release_vy = 0.0
        self.time = 0.0
        self.mood = PetMood.CALM
        self.mood_timer = 0.0
        self.mood_interval = 30 * 60
        self.message = ""
        self.message_timer = 0.0
        self.message_duration = 8.0
        self._last_click = 0.0

    @property
    def speed(self):
        return self.settings.animationSpeed

    def set_action(self, action: PetAction, duration: float = 0.0):
        if action == self.current_action and self.transition_progress >= 1.0:
            return
        self.target_action = action
        # 玩耍形态需要快速而柔和地在伊娃和火箭之间交叉淡化；普通动作
        # 保持更舒缓的过渡，符合桌面陪伴场景。
        self.transition_duration = 0.8 if PetAction.PLAY in (self.current_action, action) else 1.8
        self.transition_progress = 0.0
        self.action_timer = 0.0
        self.action_duration = duration

    def random_action(self):
        choices = [a for a in PetAction if a != self.current_action]
        action = random.choice(choices)
        duration = 16.0 if action == PetAction.SLEEP else 12.0
        self.set_action(action, duration)

    def start_drag(self):
        self.dragging = True
        self.drag_vx = 0.0
        self.drag_vy = 0.0
        self._last_drag_dx = 0.0
        self._last_drag_dy = 0.0

    def update_drag(self, dx, dy):
        self.drag_offset_x = dx
        self.drag_offset_y = dy
        # 平滑速度：本次位移与历史速度加权，形成"甩动感"
        self.drag_vx = self.drag_vx * 0.62 + (dx - self._last_drag_dx) * 0.38
        self.drag_vy = self.drag_vy * 0.62 + (dy - self._last_drag_dy) * 0.38
        self._last_drag_dx = dx
        self._last_drag_dy = dy
        if abs(dx) > 4 or abs(dy) > 4:
            if abs(dx) > abs(dy):
                self.drag_dir = "left" if dx < 0 else "right"
            else:
                self.drag_dir = "up" if dy < 0 else "down"

    def end_drag(self):
        self.dragging = False
        self.drag_release_time = self.time
        self._release_vx = max(-30.0, min(30.0, self.drag_vx))
        self._release_vy = max(-30.0, min(30.0, self.drag_vy))
        self.drag_dir = None

    def show_message(self, text: str, duration: float = 8.0):
        self.message = text
        self.message_timer = 0.0
        self.message_duration = duration

    def tick(self, dt: float):
        self.time += dt
        # 拖拽速度衰减：手停住后倾角平滑回正
        if self.dragging:
            decay = max(0.0, 1.0 - dt * 9.0)
            self.drag_vx *= decay
            self.drag_vy *= decay
        # Transition between actions
        if self.transition_progress < 1.0:
            self.transition_progress = min(1.0, self.transition_progress + dt / self.transition_duration)
            if self.transition_progress >= 1.0:
                self.current_action = self.target_action

        # Action auto-return
        if self.action_duration > 0 and not self.dragging:
            self.action_timer += dt
            if self.action_timer >= self.action_duration:
                self.action_duration = 0.0
                if self.current_action != PetAction.IDLE:
                    self.set_action(PetAction.IDLE)

        # Mood auto switch（间隔跟随设置实时生效）
        if self.settings.moodAutoSwitch:
            self.mood_interval = self.settings.moodIntervalMinutes * 60
            self.mood_timer += dt
            if self.mood_timer >= self.mood_interval:
                self.mood_timer = 0.0
                self._switch_mood()

        # Message timer
        if self.message:
            self.message_timer += dt
            if self.message_timer >= self.message_duration:
                self.message = ""

    def _switch_mood(self):
        moods = list(PetMood)
        weights = [1 if m != self.mood else 0 for m in moods]
        total = sum(weights)
        if total > 0:
            r = random.random() * total
            acc = 0.0
            for m, w in zip(moods, weights):
                acc += w
                if r <= acc:
                    self.mood = m
                    msgs = MOOD_MESSAGES.get(self.mood, [])
                    if msgs:
                        self.show_message(random.choice(msgs))
                    break

    def get_current_pose(self) -> Pose:
        t = self.time * self.speed
        a = self.current_action
        b = self.target_action
        p = self.transition_progress
        pose_a = self._compute_pose(a, t)
        pose_b = self._compute_pose(b, t)
        if p >= 1.0:
            pose = pose_a
        else:
            # ease-in-out
            p2 = p * p * (3 - 2 * p)
            pose = Pose(
                x=pose_a.x + (pose_b.x - pose_a.x) * p2,
                y=pose_a.y + (pose_b.y - pose_a.y) * p2,
                rotation=pose_a.rotation + (pose_b.rotation - pose_a.rotation) * p2,
                scale=pose_a.scale + (pose_b.scale - pose_a.scale) * p2,
                eye_alpha=1.0,
                head_x=pose_a.head_x + (pose_b.head_x - pose_a.head_x) * p2,
                head_y=pose_a.head_y + (pose_b.head_y - pose_a.head_y) * p2,
                head_rotation=pose_a.head_rotation + (pose_b.head_rotation - pose_a.head_rotation) * p2,
            )
        # 松手回弹：带阻尼的左右/上下震荡，模拟"掉下来晃两下"
        if not self.dragging:
            d = self.time - self.drag_release_time
            if 0 <= d < 1.5:
                decay = math.exp(-3.2 * d)
                wobble = math.cos(d * 13.0)
                pose = Pose(
                    x=pose.x,
                    y=pose.y + self._release_vy * 0.5 * decay * wobble,
                    rotation=pose.rotation - self._release_vx * 0.7 * decay * wobble,
                    scale=pose.scale,
                    eye_alpha=pose.eye_alpha,
                    head_x=pose.head_x,
                    head_y=pose.head_y,
                    head_rotation=pose.head_rotation,
                )
        return pose

    def transition_eased(self) -> float:
        """当前动作过渡的 smoothstep 进度，供姿态、眼睛和形态共用。"""
        if self.transition_progress >= 1.0:
            return 1.0
        p = max(0.0, min(1.0, self.transition_progress))
        return p * p * (3.0 - 2.0 * p)

    def action_layers(self):
        """返回需要绘制的动作及透明度；过渡期间恰好两层且总和为 1。"""
        if self.transition_progress >= 1.0 or self.current_action == self.target_action:
            return [(self.current_action, 1.0)]
        p = self.transition_eased()
        return [(self.current_action, 1.0 - p), (self.target_action, p)]

    def action_opacity(self, action: PetAction) -> float:
        return sum(alpha for layer_action, alpha in self.action_layers() if layer_action == action)

    def pose_for(self, action: PetAction) -> Pose:
        """取得指定动作当前时刻的独立姿态，用于火箭形态无闪烁交叉淡化。"""
        return self._compute_pose(action, self.time * self.speed)

    def _compute_pose(self, action: PetAction, t: float) -> Pose:
        if self.dragging:
            return self._drag_pose(t, action)

        if action == PetAction.IDLE:
            # 待机：自然呼吸站姿，头部稳定，身体轻微浮动
            body_sway = math.sin(t * 0.12) * 5
            body_bob = math.sin(t * 0.18) * 8
            body_rot = math.sin(t * 0.10) * 3
            # 头部与身体做轻微反向运动，模拟颈椎自然平衡
            head_sway = math.sin(t * 0.12 - 0.6) * 3
            head_bob = math.sin(t * 0.18 - 0.5) * 4
            head_rot = math.sin(t * 0.10 - 0.5) * -1.5
            return Pose(
                x=body_sway,
                y=body_bob,
                rotation=body_rot,
                scale=1 + math.sin(t * 0.16) * 0.020,
                head_x=head_sway,
                head_y=head_bob,
                head_rotation=head_rot,
            )
        elif action == PetAction.HOVER:
            return Pose(
                x=math.sin(t * 0.16) * 52,
                y=-20 + math.sin(t * 0.24) * 24,
                rotation=math.cos(t * 0.16) * 10,
                scale=1.035 + math.sin(t * 0.18) * 0.030,
            )
        elif action == PetAction.CHEER:
            return Pose(
                x=math.sin(t * 0.34) * 14,
                y=-10 - abs(math.sin(t * 0.43)) * 36,
                rotation=math.sin(t * 0.34) * 12,
                scale=1.05 + abs(math.sin(t * 0.43)) * 0.055,
            )
        elif action == PetAction.PLAY:
            p = t * 0.5
            size_scale = max(0.64, min(2.36, self.settings.size / 220.0))
            dx = math.cos(p) * 84 * size_scale
            dy = math.cos(2 * p) * 100 * size_scale
            return Pose(
                x=math.sin(p) * 84 * size_scale,
                y=(math.sin(2 * p) * 50 - 18) * size_scale,
                # 火箭素材默认朝上，因此在轨迹切线角度基础上增加 90°。
                rotation=math.degrees(math.atan2(dy, dx)) + 90.0,
                scale=1.0 + abs(math.sin(2 * p)) * 0.04,
            )
        elif action == PetAction.SLEEP:
            return Pose(
                x=math.sin(t * 0.06) * 6,
                y=10 + math.sin(t * 0.10) * 8,
                rotation=-5 + math.sin(t * 0.08) * 2.5,
                scale=0.965 + math.sin(t * 0.09) * 0.020,
                eye_alpha=0.45,
            )
        return Pose()

    def _drag_pose(self, t: float, action: PetAction) -> Pose:
        """被拖拽的感觉：朝移动方向倾斜、身体有底部推进感，
        头部因为惯性明显滞后于身体，像被一只无形的手拽着脑袋。
        推进力从底部发射器向上，速度快时身体微上抬。"""
        vx = max(-30.0, min(30.0, self.drag_vx))
        vy = max(-30.0, min(30.0, self.drag_vy))
        speed = math.hypot(vx, vy)
        # 身体整体倾斜：快速移动时朝运动方向反方向倾斜
        if action == PetAction.PLAY and speed > 0.4:
            # 火箭头部始终朝拖动方向；素材的自然方向为向上。
            rot = math.degrees(math.atan2(vy, vx)) + 90.0
        else:
            rot = max(-28.0, min(28.0, -vx * 0.90))
        # 身体滞后：比头部少滞后一点，制造"头被甩在后面"的感觉
        lag_x = -vx * 0.32
        lag_y = vy * 0.40
        # 底部推进：速度快时身体被托起
        lift = -min(8.0, speed * 0.30)
        # 拉伸：水平方向被甩长，垂直方向被压扁
        stretch_x = 1.0 + min(0.08, speed / 520.0)
        bob = math.sin(t * 1.1) * 2
        # 头部独立滞后：比身体更慢跟上鼠标，产生被拽感
        head_lag_x = -vx * 0.75
        head_lag_y = vy * 0.55 + min(6.0, speed * 0.25)  # 速度快时头还会微微下坠
        head_rot = -vx * 0.45
        head_rot = max(-18.0, min(18.0, head_rot))
        return Pose(
            x=max(-12.0, min(12.0, lag_x)),
            y=lift + max(-16.0, min(16.0, lag_y)) + bob,
            rotation=rot,
            scale=stretch_x,
            head_x=max(-22.0, min(22.0, head_lag_x)),
            head_y=max(-14.0, min(14.0, head_lag_y)),
            head_rotation=head_rot,
        )

    def current_eye_asset(self):
        return self.current_action.value + ".svg"

    @staticmethod
    def accent_for(action: PetAction):
        return {
            PetAction.IDLE: "#70C2EB",
            PetAction.HOVER: "#85A8E8",
            PetAction.CHEER: "#85DBB3",
            PetAction.PLAY: "#57C7FA",
            PetAction.SLEEP: "#A89EDB",
        }.get(action, "#70C2EB")

    def current_accent(self):
        layers = self.action_layers()
        if len(layers) == 1:
            return self.accent_for(layers[0][0])
        (a, aa), (b, ab) = layers
        # QColor 不应成为领域层依赖；返回手工插值后的十六进制颜色。
        rgb_a = tuple(int(self.accent_for(a)[i:i + 2], 16) for i in (1, 3, 5))
        rgb_b = tuple(int(self.accent_for(b)[i:i + 2], 16) for i in (1, 3, 5))
        rgb = tuple(round(x * aa + y * ab) for x, y in zip(rgb_a, rgb_b))
        return "#%02X%02X%02X" % rgb
