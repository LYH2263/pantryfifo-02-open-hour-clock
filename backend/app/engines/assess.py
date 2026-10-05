"""Single source of truth for a lot's freshness determination.

Every surface that talks about a lot being expired / expiring — the top
alert bar, the layer-page badge, the expire sweep — must derive from
assess_lot(). One lot row, one `now`, one determination.

Rules:
- Unopened lots follow the calendar only: expired once `today > expiry`,
  `soon` when days_left <= warn_days.
- Opened lots carry a pinned `open_hours` budget (fixed at confirm time,
  immune to later settings changes). Their opened deadline is
  opened_at + open_hours; the effective deadline is min(calendar, opened).
- `reason` says which constraint binds: "calendar" or "opened". When the
  two deadlines are within _TIE_SECONDS the calendar is treated as binding
  (the pinned hours were already capped by the calendar at open time).
"""
from datetime import date, datetime, time, timedelta

_TIE_SECONDS = 60


def calendar_deadline(expiry: str, tz) -> datetime:
    """End of the expiry day: a lot is calendar-fresh through its expiry date."""
    d = date.fromisoformat(expiry)
    return datetime.combine(d + timedelta(days=1), time.min, tzinfo=tz)


def opened_deadline(lot: dict) -> datetime | None:
    """opened_at + pinned open_hours, or None for unopened lots."""
    if lot.get("opened_at") and lot.get("open_hours") is not None:
        opened_at = datetime.fromisoformat(lot["opened_at"])
        return opened_at + timedelta(hours=float(lot["open_hours"]))
    return None


def pin_hours(lot: dict, now: datetime, default_hours: float) -> float:
    """Hours an open-confirm would pin for this lot right now: the default
    budget capped by the calendar deadline, floored to milliseconds-of-hour
    so the pinned deadline never lands past the calendar deadline."""
    hours = float(default_hours)
    expiry = lot.get("expiry")
    if expiry:
        cal = calendar_deadline(expiry, now.tzinfo)
        cal_remaining = (cal - now).total_seconds() / 3600
        hours = min(hours, cal_remaining)
    if hours <= 0:
        return 0.0
    # floor to 3 decimals (3.6s granularity) — never rounds up past calendar
    return int(hours * 1000) / 1000


def assess_lot(lot: dict, now: datetime, warn_days: int) -> dict:
    """The one determination for a lot. `now` must be tz-aware.

    Returns level in {"ok","soon","expired"}, reason in {"","calendar","opened"},
    plus display fields shared by the alert bar and the layer badge.
    """
    expiry = lot.get("expiry")
    cal_deadline = calendar_deadline(expiry, now.tzinfo) if expiry else None
    op_deadline = opened_deadline(lot)
    days_left = (date.fromisoformat(expiry) - now.date()).days if expiry else None
    remaining_hours = (
        (op_deadline - now).total_seconds() / 3600 if op_deadline is not None else None
    )

    opened_binds = False
    if op_deadline is not None:
        opened_binds = cal_deadline is None or (
            op_deadline - cal_deadline
        ).total_seconds() < -_TIE_SECONDS

    deadlines = [d for d in (cal_deadline, op_deadline) if d is not None]
    effective = min(deadlines) if deadlines else None

    if effective is not None and now >= effective:
        level = "expired"
    else:
        cal_soon = days_left is not None and 0 <= days_left <= warn_days
        op_soon = remaining_hours is not None and 0 < remaining_hours <= warn_days * 24
        level = "soon" if (cal_soon or op_soon) else "ok"

    reason = ""
    if level != "ok":
        reason = "opened" if opened_binds else "calendar"

    return {
        "level": level,
        "reason": reason,
        "days_left": days_left,
        "opened_remaining_hours": remaining_hours,
        "effective_deadline": effective.isoformat() if effective else None,
    }


def expire_lots(lots: list[dict], now: datetime, warn_days: int) -> list[int]:
    """Ids that should leave shelf: positive remaining and assess() == expired
    (calendar-expired or opened-timed-out — same determination as the bar)."""
    out = []
    for lot in lots:
        if float(lot.get("qty_remain", 0)) <= 0:
            continue
        if assess_lot(lot, now, warn_days)["level"] == "expired":
            out.append(lot["id"])
    return out
