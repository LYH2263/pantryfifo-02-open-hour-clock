"""assess_lot 判定引擎：开封钉小时、双截止取早、单次判定一致。"""
from datetime import datetime, timedelta, timezone

from app.engines.lot_status import assess_lot, hard_expire_reason, open_deadline

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def lot(opened_hours_ago=None, pinned=None, expiry="2030-01-01", qty=1.0):
    out = {"id": 1, "qty_remain": qty, "expiry": expiry}
    if opened_hours_ago is not None:
        out["opened_at"] = (NOW - timedelta(hours=opened_hours_ago)).isoformat()
        out["open_hours"] = pinned
    return out


def test_unopened_only_calendar():
    a = assess_lot(lot(expiry="2026-10-10"), now=NOW)
    assert a["opened"] is False and a["open_deadline"] is None
    assert a["reason_code"] in ("calendar_soon", "")

    expired = assess_lot(lot(expiry="2026-10-01"), now=NOW)
    assert expired["level"] == "expired" and expired["reason_code"] == "calendar_expired"
    assert expired["reason"] == "日历到期"


def test_opened_not_overdue_keeps_hours():
    a = assess_lot(lot(opened_hours_ago=5, pinned=48), now=NOW)
    assert a["opened"] and a["level"] == "ok"
    assert a["hours_left"] == 43.0 and a["open_deadline"] is not None


def test_opened_overdue_while_calendar_far():
    a = assess_lot(lot(opened_hours_ago=60, pinned=48, expiry="2030-01-01"), now=NOW)
    assert a["level"] == "expired"
    assert a["reason_code"] == "open_expired" and a["reason"] == "开封超时"
    assert hard_expire_reason(lot(opened_hours_ago=60, pinned=48, expiry="2030-01-01"), now=NOW) == "open_expired"


def test_both_overdue_earlier_deadline_wins():
    # 日历 2026-10-01 先到（10-04 前 3 天=72h），开封 60h 前/钉 48h（48h 前超时）
    cal_first = assess_lot(lot(opened_hours_ago=60, pinned=48, expiry="2026-10-01"), now=NOW)
    assert cal_first["reason_code"] == "calendar_expired"

    # 开封超时更早：开封 200h 前/钉 48（152h 前超时），日历 2026-10-03（1 天前）
    open_first = assess_lot(lot(opened_hours_ago=200, pinned=48, expiry="2026-10-03"), now=NOW)
    assert open_first["reason_code"] == "open_expired"


def test_pinned_hours_independent_of_settings():
    # 引擎只认批上钉住的 open_hours；“改默认小时”没有任何通道能改它
    a = assess_lot(lot(opened_hours_ago=5, pinned=48), now=NOW)
    assert a["hours_left"] == 43.0
    same = lot(opened_hours_ago=5, pinned=48)
    assert open_deadline(same) == NOW + timedelta(hours=43)
