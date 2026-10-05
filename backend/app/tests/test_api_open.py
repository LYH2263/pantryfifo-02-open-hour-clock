"""开封小时模块端到端：预览/确认、同一次判定、改设置不回溯、三路写入竞争。"""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import seed
    from app.main import app
    seed.init_db()
    with TestClient(app) as c:
        yield c


def insert_lot(c, *, expiry="2030-01-01", qty=2.0, opened_hours_ago=None, pinned=None,
               item_id=1, status="on_shelf"):
    """直接写一条测试批，返回 id。c 是 TestClient（借 /health 无关），这里用 db 连接。"""
    from app.db import connect
    opened_at = None
    if opened_hours_ago is not None:
        opened_at = (datetime.now(timezone.utc) - timedelta(hours=opened_hours_ago)).isoformat()
    conn = connect()
    cur = conn.execute(
        """INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,opened_at,open_hours)
           VALUES (?,?,?,?,?,?,?,?)""",
        (item_id, qty, qty, expiry, status, "clean", opened_at, pinned))
    conn.commit()
    lid = cur.lastrowid
    conn.close()
    return lid


def get_lot(lot_id):
    from app.db import connect
    conn = connect()
    row = dict(conn.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone())
    conn.close()
    return row


# ---------- 预览：只报钉住的剩余小时，qty_remain 保持 ----------

def test_open_preview_reports_only_pinned_hours_and_keeps_qty(client):
    lid = insert_lot(client, qty=2.0)
    r = client.get(f"/api/lots/{lid}/open-preview")
    assert r.status_code == 200
    body = r.json()
    assert body["pinned_open_hours"] == 48.0
    assert body["qty_remain"] == 2.0
    row = get_lot(lid)
    assert row["opened_at"] is None and row["qty_remain"] == 2.0  # 预览零写入


def test_preview_rejects_already_opened(client):
    lid = insert_lot(client, opened_hours_ago=1, pinned=48)
    assert client.get(f"/api/lots/{lid}/open-preview").status_code == 409


# ---------- 确认：开封不扣减余量 ----------

def test_open_confirm_pins_hours_without_deduction(client):
    lid = insert_lot(client, qty=2.0)
    r = client.post(f"/api/lots/{lid}/open")
    assert r.status_code == 200
    body = r.json()
    assert body["qty_unchanged"] is True
    assert body["qty_remain"] == 2.0
    assert body["opened"] is True and body["open_hours"] == 48.0
    row = get_lot(lid)
    assert row["qty_remain"] == 2.0 and row["opened_at"] and row["open_hours"] == 48.0


def test_open_consumed_or_expired_lot_conflicts(client):
    lid1 = insert_lot(client, qty=1.0, status="consumed")
    assert client.post(f"/api/lots/{lid1}/open").status_code == 409
    lid2 = insert_lot(client, qty=0.0)
    assert client.post(f"/api/lots/{lid2}/open").status_code == 409


# ---------- 顶条原因字与层页角标必须属于同一次判定 ----------

def test_alerts_and_fridge_share_one_judgment_open_overdue(client):
    lid = insert_lot(client, expiry="2030-01-01", opened_hours_ago=60, pinned=48)
    f = {x["id"]: x for x in client.get("/api/fridge").json()}
    a = {x["id"]: x for x in client.get("/api/alerts").json()}
    assert lid in f and lid in a
    for key in ("level", "reason_code", "reason"):
        assert f[lid][key] == a[lid][key]
    assert a[lid]["reason_code"] == "open_expired" and a[lid]["reason"] == "开封超时"


def test_unopened_lot_has_no_open_deadline(client):
    lid = insert_lot(client, expiry="2030-01-01")
    f = {x["id"]: x for x in client.get("/api/fridge").json()}
    assert f[lid]["opened"] is False and f[lid]["open_deadline"] is None
    assert f[lid]["reason_code"] != "open_expired"


def test_earlier_deadline_is_the_single_reason(client):
    # 日历已到期、开封未超时 → 日历到期是唯一天平
    lid = insert_lot(client, expiry="2000-01-01", opened_hours_ago=1, pinned=48)
    f = {x["id"]: x for x in client.get("/api/fridge").json()}
    assert f[lid]["reason_code"] == "calendar_expired" and f[lid]["reason"] == "日历到期"


# ---------- 下架走同一判定 ----------

def test_sweep_handles_both_reasons_consistently(client):
    cal = insert_lot(client, expiry="2000-01-01")
    opn = insert_lot(client, expiry="2030-01-01", opened_hours_ago=60, pinned=48)
    r = client.post("/api/expire-sweep")
    reasons = {m["lot_id"]: m["reason_code"] for m in r.json()["expired"]}
    assert reasons[cal] == "calendar_expired"
    assert reasons[opn] == "open_expired"
    assert get_lot(opn)["status"] == "expired"
    # 下架后顶条与层页都不再显示该批
    on_shelf = {x["id"] for x in client.get("/api/fridge").json()}
    assert cal not in on_shelf and opn not in on_shelf


# ---------- 改默认开封小时：已开封批（含已超时待下架）不回溯 ----------

def test_changing_default_hours_does_not_retrofit(client):
    fresh = insert_lot(client, opened_hours_ago=5, pinned=48, expiry="2030-01-01")   # 已开封未超时
    waiting = insert_lot(client, opened_hours_ago=60, pinned=48, expiry="2030-01-01")  # 已超时待下架
    not_yet = insert_lot(client, expiry="2030-01-01")

    r = client.put("/api/settings", json={"default_open_hours": 12})
    assert r.status_code == 200 and float(r.json()["default_open_hours"]) == 12.0

    assert get_lot(fresh)["open_hours"] == 48.0
    assert get_lot(waiting)["open_hours"] == 48.0

    a = {x["id"]: x for x in client.get("/api/alerts").json()}
    f = {x["id"]: x for x in client.get("/api/fridge").json()}
    # 未超时批仍按钉住的 48h：剩余约 43h，不报警
    assert fresh not in a and f[fresh]["hours_left"] == pytest.approx(43, abs=0.5)
    # 已超时待下架批：顶条仍按钉住值喊开封超时，角标同因，没有停在旧日历/丢失
    assert a[waiting]["reason_code"] == "open_expired"
    assert f[waiting]["reason_code"] == a[waiting]["reason_code"]

    # sweep 同样按钉住值把它带走，响应原因与角标一致
    swept = client.post("/api/expire-sweep").json()
    assert {"lot_id": waiting, "reason_code": "open_expired"} in \
           [{"lot_id": m["lot_id"], "reason_code": m["reason_code"]} for m in swept["expired"]]
    assert get_lot(waiting)["status"] == "expired"

    # 改设置之后才开封的批钉新值 12h
    client.post(f"/api/lots/{not_yet}/open")
    assert get_lot(not_yet)["open_hours"] == 12.0


def test_invalid_settings_rejected(client):
    assert client.put("/api/settings", json={"default_open_hours": 0}).status_code == 400
    assert client.put("/api/settings", json={"warn_days": -1}).status_code == 400


# ---------- 三路写入：开封 / 消费 / 过期下架 同时打同一批 ----------

def new_item():
    from app.db import connect
    conn = connect()
    cur = conn.execute("INSERT INTO items(name,layer,unit) VALUES (?,?,?)",
                       ("race_item", "mid", "个"))
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return iid


def test_three_way_write_race_leaves_one_consistent_outcome(client):
    """对日历到期批同时：开封确认 / FEFO 扣完 / 过期下架，重复 15 轮。"""
    rounds = 15
    for i in range(rounds):
        item_id = new_item()
        lid = insert_lot(client, expiry="2000-01-01", qty=1.0, item_id=item_id)
        barrier = threading.Barrier(3)
        results = {}

        def do_open():
            barrier.wait()
            results["open"] = client.post(f"/api/lots/{lid}/open").status_code

        def do_consume():
            barrier.wait()
            r = client.post("/api/consume", json={"item_id": item_id, "qty": 1.0})
            results["consume"] = r.status_code

        def do_sweep():
            barrier.wait()
            r = client.post("/api/expire-sweep")
            results["sweep"] = (r.status_code, r.json() if r.status_code == 200 else None)

        ts = [threading.Thread(target=do_open), threading.Thread(target=do_consume),
              threading.Thread(target=do_sweep)]
        for t in ts: t.start()
        for t in ts: t.join()

        # 不允许 500：锁竞争必须表现为串行成功或 409，不能炸
        assert results["open"] in (200, 409), results
        assert results["consume"] in (200, 409), results
        assert results["sweep"][0] == 200, results

        row = get_lot(lid)
        # 开封标记必须成对：不可能只写了 opened_at 没钉 open_hours
        assert (row["opened_at"] is None) == (row["open_hours"] is None)
        # 三笔都跑完后只会留一种结局：consumed 与 expired 互斥
        assert row["status"] in ("consumed", "expired")
        if row["status"] == "consumed":
            assert row["qty_remain"] == 0
            assert results["consume"] == 200
        else:
            assert row["qty_remain"] == 1.0
            assert any(m["lot_id"] == lid for m in results["sweep"][1]["expired"])
        # 顶条与层页都不得再喊已离架的批，杜绝“已 consumed 却喊开封超时”
        ids = {x["id"] for x in client.get("/api/alerts").json()}
        assert lid not in ids


def test_open_vs_consume_partial_take_keeps_marker_and_qty_consistent(client):
    """开封与部分消费竞争：若开封得手，批在架、已开封、余量只被扣一次。"""
    item_id = new_item()
    lid = insert_lot(client, expiry="2030-01-01", qty=3.0, item_id=item_id)
    barrier = threading.Barrier(2)
    out = {}

    def do_open():
        barrier.wait(); out["open"] = client.post(f"/api/lots/{lid}/open").status_code

    def do_consume():
        barrier.wait()
        r = client.post("/api/consume", json={"item_id": item_id, "qty": 1.0})
        out["consume"] = r.status_code

    ts = [threading.Thread(target=do_open), threading.Thread(target=do_consume)]
    for t in ts: t.start()
    for t in ts: t.join()

    assert out["open"] in (200, 409) and out["consume"] in (200, 409)
    row = get_lot(lid)
    assert row["status"] == "on_shelf" and row["qty_remain"] == 2.0
    if out["open"] == 200:
        assert row["opened_at"] and row["open_hours"] == 48.0
    # 无论顺序：角标与（若临期）顶条口径一致
    f = {x["id"]: x for x in client.get("/api/fridge").json()}
    assert f[lid]["opened"] is (out["open"] == 200)
    assert f[lid]["qty_remain"] == 2.0
