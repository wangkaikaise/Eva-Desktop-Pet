import time
import datetime
from typing import List, Callable

from settings import PetReminder


class ReminderScheduler:
    def __init__(self, reminders: List[PetReminder], on_trigger: Callable[[PetReminder], None]):
        self.reminders = reminders
        self.on_trigger = on_trigger
        self._last_check = time.time()
        # 间隔提醒从应用启动时起算，避免每次启动立即弹出。
        self._triggered = {
            r.id: self._last_check for r in reminders if r.schedule == "interval"
        }  # id -> last trigger timestamp

    def update(self):
        now = time.time()
        now_dt = datetime.datetime.now()
        if now < self._last_check:
            # 系统时钟向后调整时重建基准，防止重复补发。
            self._last_check = now
        for r in self.reminders:
            if not r.isEnabled:
                continue
            key = r.id
            last = self._triggered.get(key, 0)
            due = False
            if r.schedule == "daily":
                target = now_dt.replace(hour=r.hour, minute=r.minute, second=0, microsecond=0)
                target_ts = target.timestamp()
                # 正常轮询或电脑睡眠后恢复时，只补发本轮检查区间内错过的一次。
                if self._last_check < target_ts <= now and target_ts > last:
                    due = True
            elif r.schedule == "interval":
                if last == 0:
                    due = True
                elif now - last >= r.intervalMinutes * 60:
                    due = True
            if due:
                self._triggered[key] = now
                self.on_trigger(r)
        self._last_check = now

    def rebuild(self, reminders: List[PetReminder]):
        self.reminders = reminders
        # 清理已删除提醒的触发记录
        ids = {r.id for r in reminders}
        self._triggered = {k: v for k, v in self._triggered.items() if k in ids}
        # 新建的间隔提醒从"现在"起算，避免刚添加就立刻触发一次
        now = time.time()
        for r in reminders:
            if r.id not in self._triggered:
                self._triggered[r.id] = now
