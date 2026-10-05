import threading
from datetime import datetime, timedelta, timezone

import pytest

from app import lotstore, seed
from app.db import connect
from app.lotstore import LotError

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    c = connect()
    c.execute("DELETE FROM lots")
    c.close()
    return tmp_path


def add_lot(item_id=1, qty=5, expiry="2026-11-01", status="on_shelf",
            opened_at=None, open_hours=None):
    c = connect()
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,opened_at,open_hours)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (item_id, qty, qty, expiry, status, "clean", opened_at, open_hours))
    lid = cur.lastrowid
    c.close()
    return lid


def lot_row(lid):
    c = connect()
    row = dict(c.execute("SELECT * FROM lots WHERE id=?", (lid,)).fetchone())
    c.close()
    return row


# ---- open: preview pins hours, confirm keeps qty

def test_preview_reports_only_pinned_hours(db):
    lid = add_lot(qty=5, expiry="2026-11-01")
    p = lotstore.open_preview(lid, now=NOW)
    assert p == {"lot_id": lid, "hours": 48.0}
    assert "qty_remain" not in p


def test_preview_capped_by_calendar(db):
    lid = add_lot(qty=5, expiry="2026-10-04")  # end of day = 12h from noon
    assert lotstore.open_preview(lid, now=NOW)["hours"] == 12.0


def test_open_confirm_keeps_qty_and_pins(db):
    lid = add_lot(qty=5, expiry="2026-11-01")
    r = lotstore.open_confirm(lid, now=NOW)
    assert r["qty_remain"] == 5 and r["open_hours"] == 48.0
    row = lot_row(lid)
    assert row["qty_remain"] == 5 and row["opened_at"] == NOW.isoformat()


def test_open_twice_rejected(db):
    lid = add_lot()
    lotstore.open_confirm(lid, now=NOW)
    with pytest.raises(LotError) as e:
        lotstore.open_confirm(lid, now=NOW)
    assert e.value.status == 409 and e.value.detail == "already_opened"


def test_open_expired_lot_rejected(db):
    lid = add_lot(expiry="2026-10-01")
    with pytest.raises(LotError) as e:
        lotstore.open_confirm(lid, now=NOW)
    assert e.value.detail == "already_expired"


def test_consume_keeps_opened_flag(db):
    lid = add_lot(qty=5)
    lotstore.open_confirm(lid, now=NOW)
    lotstore.consume(1, 2, now=NOW)
    row = lot_row(lid)
    assert row["qty_remain"] == 3 and row["opened_at"] is not None


# ---- one determination: bar and badge agree, consumed lots vanish from both

def test_fridge_and_alerts_same_determination(db):
    a = add_lot(expiry="2026-10-01")                       # calendar expired
    b = add_lot(expiry="2027-01-01")                       # opened timeout
    lotstore.open_confirm(b, now=NOW - timedelta(hours=50))
    c = add_lot(expiry="2026-10-06")                       # calendar soon
    add_lot(expiry="2027-01-01")                           # ok, no alert
    fridge = {r["id"]: r for r in lotstore.list_fridge(now=NOW)}
    alerts = {r["id"]: r for r in lotstore.list_alerts(now=NOW)}
    assert set(alerts) == {a, b, c}
    assert alerts[a]["alert_reason"] == "calendar"
    assert alerts[b]["alert_reason"] == "opened"
    for lid, row in alerts.items():
        assert fridge[lid]["alert_level"] == row["alert_level"]
        assert fridge[lid]["alert_reason"] == row["alert_reason"]


def test_consumed_lot_leaves_bar_and_shelf(db):
    lid = add_lot(qty=2, expiry="2026-10-01")  # expired-but-on-shelf, would alert
    assert any(r["id"] == lid for r in lotstore.list_alerts(now=NOW))
    lotstore.consume(1, 2, now=NOW)
    assert lot_row(lid)["status"] == "consumed"
    assert not any(r["id"] == lid for r in lotstore.list_alerts(now=NOW))
    assert not any(r["id"] == lid for r in lotstore.list_fridge(now=NOW))


# ---- settings change: pinned lots are immune, new opens use the new default

def test_settings_change_keeps_pinned_lots(db):
    lot_a = add_lot(expiry="2026-12-01")
    lotstore.open_confirm(lot_a, now=NOW)  # pinned 48 (seed default)
    lotstore.update_settings({"open_hours_default": 24})
    lot_b = add_lot(expiry="2026-12-01")
    lotstore.open_confirm(lot_b, now=NOW)
    # already-timeout lot, pinned under the old default, waiting for removal
    lot_c = add_lot(expiry="2026-12-01",
                    opened_at=(NOW - timedelta(hours=50)).isoformat(), open_hours=48.0)
    lotstore.update_settings({"open_hours_default": 200})

    assert lot_row(lot_a)["open_hours"] == 48.0   # pinned, does not follow the setting
    assert lot_row(lot_b)["open_hours"] == 24.0   # new opens use the current default
    assert lot_row(lot_c)["open_hours"] == 48.0

    later = NOW + timedelta(hours=30)
    expired = set(lotstore.sweep(now=later))
    assert lot_c in expired        # timed out under its pinned hours: still removed
    assert lot_b in expired        # 24h pinned < 30h
    assert lot_a not in expired    # 48h pinned > 30h: survives the settings change
    alerts = lotstore.list_alerts(now=later)
    assert not any(r["id"] in (lot_b, lot_c) for r in alerts)  # swept lots leave the bar


def test_settings_validation(db):
    with pytest.raises(LotError):
        lotstore.update_settings({"warn_days": -1})
    with pytest.raises(LotError):
        lotstore.update_settings({"open_hours_default": 0})
    with pytest.raises(LotError):
        lotstore.update_settings({})


# ---- three-way race: open vs consume vs sweep leave exactly one state

def test_three_way_write_race(db):
    for round_ in range(20):
        lid = add_lot(qty=5, expiry="2026-11-01")
        barrier = threading.Barrier(3)
        errors = []

        def guarded(fn):
            def run():
                barrier.wait()
                try:
                    fn()
                except LotError as e:
                    errors.append(e.status)
            return run

        threads = [
            threading.Thread(target=guarded(lambda: lotstore.open_confirm(lid, now=NOW))),
            threading.Thread(target=guarded(lambda: lotstore.consume(1, 5, now=NOW))),
            threading.Thread(target=guarded(
                lambda: lotstore.sweep(now=NOW + timedelta(days=100)))),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        row = lot_row(lid)
        # exactly one qty_remain / status / opened flag — a state some
        # serialization of the three writers could have produced
        assert row["status"] in ("on_shelf", "consumed", "expired")
        assert row["qty_remain"] >= 0
        if row["status"] == "consumed":
            assert row["qty_remain"] == 0
        if row["opened_at"] is None:
            assert row["open_hours"] is None
        else:
            assert row["open_hours"] is not None
        # whatever the outcome, bar and badge still agree afterwards
        fridge = {r["id"] for r in lotstore.list_fridge(now=NOW)}
        alerts = {r["id"] for r in lotstore.list_alerts(now=NOW)}
        assert alerts <= fridge
        if row["status"] != "on_shelf":
            assert lid not in fridge and lid not in alerts
        # only conflict-type rejections are allowed, never a crash
        assert all(s == 409 for s in errors)

        c = connect()
        c.execute("DELETE FROM lots")
        c.close()


def test_concurrent_consumes_never_oversell(db):
    lid = add_lot(qty=5, expiry="2026-11-01")
    results, errors = [], []
    barrier = threading.Barrier(2)

    def run():
        barrier.wait()
        try:
            results.append(lotstore.consume(1, 4, now=NOW))
        except LotError as e:
            errors.append(e)

    ts = [threading.Thread(target=run), threading.Thread(target=run)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(results) == 1 and len(errors) == 1
    assert errors[0].status == 409 and errors[0].detail["reason"] == "short"
    assert lot_row(lid)["qty_remain"] == 1
