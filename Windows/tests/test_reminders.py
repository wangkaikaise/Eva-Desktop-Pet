import datetime

import reminders as reminder_module
from reminders import ReminderScheduler
from settings import PetReminder


def test_interval_reminder_does_not_fire_immediately(monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr(reminder_module.time, "time", lambda: now)
    fired = []
    item = PetReminder(id="water", schedule="interval", intervalMinutes=15)
    scheduler = ReminderScheduler([item], fired.append)
    scheduler.update()
    assert fired == []


def test_daily_reminder_catches_up_once_after_sleep(monkeypatch):
    base = datetime.datetime(2026, 8, 24, 8, 59, 50)
    now = [base.timestamp()]
    monkeypatch.setattr(reminder_module.time, "time", lambda: now[0])

    class FakeDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime.fromtimestamp(now[0], tz)

    monkeypatch.setattr(reminder_module.datetime, "datetime", FakeDateTime)
    fired = []
    item = PetReminder(id="daily", schedule="daily", hour=9, minute=0)
    scheduler = ReminderScheduler([item], fired.append)
    now[0] = datetime.datetime(2026, 8, 24, 9, 10).timestamp()
    scheduler.update()
    scheduler.update()
    assert fired == [item]
