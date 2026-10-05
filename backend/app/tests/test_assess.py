from datetime import datetime, timedelta, timezone

from app.engines.assess import assess_lot, expire_lots, pin_hours

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


def lot(**kw):
    base = {"id": 1, "qty_remain": 1, "expiry": "2026-10-10",
            "status": "on_shelf", "opened_at": None, "open_hours": None}
    base.update(kw)
    return base


def opened(hours_ago: float, pinned: float, **kw):
    kw.setdefault("opened_at", (NOW - timedelta(hours=hours_ago)).isoformat())
    kw.setdefault("open_hours", pinned)
    return lot(**kw)


# ---- unopened lots: calendar only

def test_unopened_ok():
    a = assess_lot(lot(expiry="2026-10-10"), NOW, warn_days=3)
    assert a["level"] == "ok" and a["reason"] == ""

def test_unopened_soon():
    a = assess_lot(lot(expiry="2026-10-06"), NOW, warn_days=3)
    assert a["level"] == "soon" and a["reason"] == "calendar" and a["days_left"] == 2

def test_unopened_expired():
    a = assess_lot(lot(expiry="2026-10-03"), NOW, warn_days=3)
    assert a["level"] == "expired" and a["reason"] == "calendar"

def test_unopened_expiry_day_still_fresh():
    a = assess_lot(lot(expiry="2026-10-04"), NOW, warn_days=3)
    assert a["level"] == "soon" and a["reason"] == "calendar"

def test_unopened_never_opened_reason():
    for expiry in ("2020-01-01", "2026-10-05", "2030-01-01"):
        assert assess_lot(lot(expiry=expiry), NOW, 3)["reason"] != "opened"


# ---- opened lots: pinned hours, binding reason

def test_opened_timeout_expired():
    a = assess_lot(opened(hours_ago=50, pinned=48, expiry="2026-11-01"), NOW, 3)
    assert a["level"] == "expired" and a["reason"] == "opened"

def test_opened_soon():
    a = assess_lot(opened(hours_ago=40, pinned=48, expiry="2026-11-01"), NOW, 3)
    assert a["level"] == "soon" and a["reason"] == "opened"
    assert round(a["opened_remaining_hours"], 1) == 8.0

def test_opened_calendar_binds_first():
    # opened 1h ago with 48h pinned, but calendar expiry is today -> calendar binds
    a = assess_lot(opened(hours_ago=1, pinned=48, expiry="2026-10-04"), NOW, 3)
    assert a["reason"] == "calendar"

def test_opened_calendar_expired_wins_over_opened_ok():
    a = assess_lot(opened(hours_ago=1, pinned=48, expiry="2026-10-01"), NOW, 3)
    assert a["level"] == "expired" and a["reason"] == "calendar"

def test_opened_no_expiry_still_times_out():
    a = assess_lot(opened(hours_ago=49, pinned=48, expiry=None), NOW, 3)
    assert a["level"] == "expired" and a["reason"] == "opened"


# ---- pin_hours: what the preview reports is what confirm pins

def test_pin_uses_default():
    assert pin_hours(lot(expiry="2026-11-01"), NOW, 48) == 48.0

def test_pin_capped_by_calendar():
    # expiry today -> end of day is 12h away from noon
    assert pin_hours(lot(expiry="2026-10-04"), NOW, 48) == 12.0

def test_pin_zero_when_expired():
    assert pin_hours(lot(expiry="2026-10-01"), NOW, 48) == 0.0

def test_pin_never_past_calendar():
    pinned = pin_hours(lot(expiry="2026-10-05"), NOW, 48)
    deadline = NOW + timedelta(hours=pinned)
    assert deadline <= datetime(2026, 10, 6, tzinfo=timezone.utc)


# ---- expire_lots: same determination as the bar

def test_expire_unopened_calendar():
    ids = expire_lots([
        {"id": 1, "qty_remain": 1, "expiry": "2026-10-01"},
        {"id": 2, "qty_remain": 1, "expiry": "2026-11-01"},
    ], NOW, 3)
    assert ids == [1]

def test_expire_opened_timeout_with_future_calendar():
    ids = expire_lots([
        opened(hours_ago=50, pinned=48, expiry="2027-01-01"),
    ], NOW, 3)
    assert ids == [1]

def test_expire_skips_zero_qty():
    ids = expire_lots([{"id": 1, "qty_remain": 0, "expiry": "2020-01-01"}], NOW, 3)
    assert ids == []
