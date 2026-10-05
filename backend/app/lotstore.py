"""Transactional lot operations shared by the HTTP layer.

Every write path (open-confirm, consume, expire-sweep, settings update) runs
inside db.write_tx() so concurrent requests serialize, and every read model
(fridge rows, alert rows) decorates lots through engines.assess so the top
bar and the layer badge always show the same determination.
"""
import json
from datetime import datetime, timezone

from app.db import connect, write_tx
from app.engines.assess import assess_lot, expire_lots, pin_hours
from app.engines.fefo import consume_fefo


class LotError(Exception):
    def __init__(self, status: int, detail):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _setting(c, key: str, default: str) -> str:
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _warn_days(c) -> int:
    return int(_setting(c, "warn_days", "3"))


def _open_hours_default(c) -> float:
    return float(_setting(c, "open_hours_default", "48"))


def _get_lot(c, lot_id: int):
    row = c.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
    return dict(row) if row else None


def _decorate(lot: dict, now: datetime, warn_days: int) -> dict:
    a = assess_lot(lot, now, warn_days)
    return {
        **lot,
        "opened": lot.get("opened_at") is not None,
        "alert_level": a["level"],
        "alert_reason": a["reason"],
        "days_left": a["days_left"],
        "opened_remaining_hours": a["opened_remaining_hours"],
        "effective_deadline": a["effective_deadline"],
    }


def _guard_openable(lot: dict | None, lot_id: int) -> dict:
    if lot is None:
        raise LotError(404, "lot_not_found")
    if lot["status"] != "on_shelf":
        raise LotError(409, "not_on_shelf")
    if float(lot["qty_remain"]) <= 0:
        raise LotError(409, "no_remaining_qty")
    if lot.get("opened_at") is not None:
        raise LotError(409, "already_opened")
    return lot


# ---------------------------------------------------------------- reads

def list_fridge(layer: str | None = None, now: datetime | None = None) -> list[dict]:
    now = _now(now)
    c = connect()
    try:
        q = """SELECT lots.*, items.name, items.layer, items.unit FROM lots
               JOIN items ON items.id=lots.item_id WHERE lots.status='on_shelf'"""
        args: list = []
        if layer:
            q += " AND items.layer=?"
            args.append(layer)
        rows = [dict(r) for r in c.execute(q, args)]
        warn = _warn_days(c)
    finally:
        c.close()
    return [_decorate(r, now, warn) for r in rows]


def list_alerts(now: datetime | None = None) -> list[dict]:
    now = _now(now)
    c = connect()
    try:
        rows = [dict(r) for r in c.execute(
            """SELECT lots.*, items.name, items.layer FROM lots
               JOIN items ON items.id=lots.item_id
               WHERE status='on_shelf' AND qty_remain>0""")]
        warn = _warn_days(c)
    finally:
        c.close()
    out = []
    for r in rows:
        d = _decorate(r, now, warn)
        if d["alert_level"] != "ok":
            out.append(d)
    rank = {"expired": 0, "soon": 1}
    out.sort(key=lambda d: (rank[d["alert_level"]], d["effective_deadline"] or "", d["id"]))
    return out


def get_settings() -> dict:
    c = connect()
    try:
        return {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}
    finally:
        c.close()


# ---------------------------------------------------------------- open

def open_preview(lot_id: int, now: datetime | None = None) -> dict:
    """Report only the remaining hours a confirm would pin; qty stays untouched."""
    now = _now(now)
    c = connect()
    try:
        lot = _guard_openable(_get_lot(c, lot_id), lot_id)
        hours = pin_hours(lot, now, _open_hours_default(c))
    finally:
        c.close()
    if hours <= 0:
        raise LotError(409, "already_expired")
    return {"lot_id": lot_id, "hours": hours}


def open_confirm(lot_id: int, now: datetime | None = None) -> dict:
    """Pin opened_at + open_hours on the lot. Never touches qty_remain."""
    now = _now(now)
    with write_tx() as c:
        lot = _guard_openable(_get_lot(c, lot_id), lot_id)
        hours = pin_hours(lot, now, _open_hours_default(c))
        if hours <= 0:
            raise LotError(409, "already_expired")
        cur = c.execute(
            """UPDATE lots SET opened_at=?, open_hours=?
               WHERE id=? AND status='on_shelf' AND qty_remain>0 AND opened_at IS NULL""",
            (now.isoformat(), hours, lot_id),
        )
        if cur.rowcount != 1:
            raise LotError(409, "open_conflict")
        row = _get_lot(c, lot_id)
        a = assess_lot(row, now, _warn_days(c))
        return {
            "id": lot_id,
            "opened_at": row["opened_at"],
            "open_hours": row["open_hours"],
            "qty_remain": row["qty_remain"],
            "alert_level": a["level"],
            "alert_reason": a["reason"],
        }


# ---------------------------------------------------------------- consume

def consume(item_id: int, qty: float, note: str = "", now: datetime | None = None) -> dict:
    now = _now(now)
    with write_tx() as c:
        lots = [dict(r) for r in c.execute(
            "SELECT * FROM lots WHERE item_id=? AND status='on_shelf' AND qty_remain>0",
            (item_id,))]
        result = consume_fefo(lots, qty)
        if not result["ok"] and result["reason"] == "qty_non_positive":
            raise LotError(400, result["reason"])
        if not result["ok"]:
            raise LotError(409, result)
        for d in result["deductions"]:
            cur = c.execute(
                """UPDATE lots SET qty_remain = qty_remain - ?
                   WHERE id=? AND status='on_shelf' AND qty_remain >= ?""",
                (d["take"], d["lot_id"], d["take"]),
            )
            if cur.rowcount != 1:
                raise LotError(409, "consume_conflict")
            rem = c.execute("SELECT qty_remain FROM lots WHERE id=?",
                            (d["lot_id"],)).fetchone()["qty_remain"]
            if rem <= 1e-9:
                c.execute("UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?",
                          (d["lot_id"],))
        c.execute("INSERT INTO consumptions(note,result_json,created_at) VALUES (?,?,?)",
                  (note, json.dumps(result), now.isoformat()))
        return result


# ---------------------------------------------------------------- sweep

def sweep(now: datetime | None = None) -> list[int]:
    now = _now(now)
    with write_tx() as c:
        warn = _warn_days(c)
        lots = [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
        ids = expire_lots(lots, now, warn)
        for i in ids:
            c.execute("UPDATE lots SET status='expired' WHERE id=? AND status='on_shelf'", (i,))
        return ids


# ---------------------------------------------------------------- settings

def update_settings(patch: dict) -> dict:
    """Change warn_days / open_hours_default. Already-opened lots keep the
    hours pinned on their row, so this never moves their deadlines."""
    clean: dict[str, str] = {}
    if "warn_days" in patch and patch["warn_days"] is not None:
        try:
            v = int(patch["warn_days"])
        except (TypeError, ValueError):
            raise LotError(400, "warn_days_invalid")
        if v < 0:
            raise LotError(400, "warn_days_invalid")
        clean["warn_days"] = str(v)
    if "open_hours_default" in patch and patch["open_hours_default"] is not None:
        try:
            v = float(patch["open_hours_default"])
        except (TypeError, ValueError):
            raise LotError(400, "open_hours_default_invalid")
        if v <= 0:
            raise LotError(400, "open_hours_default_invalid")
        clean["open_hours_default"] = repr(v)
    if not clean:
        raise LotError(400, "nothing_to_update")
    with write_tx() as c:
        for k, v in clean.items():
            c.execute(
                "INSERT INTO settings(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, v),
            )
    return get_settings()
