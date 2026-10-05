from app.engines.fefo import consume_fefo, sort_lots_fefo

def test_fefo_order():
    lots = [
        {"id": 2, "qty_remain": 3, "expiry": "2026-02-01"},
        {"id": 1, "qty_remain": 2, "expiry": "2026-01-10"},
    ]
    assert [l["id"] for l in sort_lots_fefo(lots)] == [1, 2]
    r = consume_fefo(lots, 3)
    assert r["ok"] and r["deductions"][0]["lot_id"] == 1 and r["deductions"][0]["take"] == 2
    assert r["deductions"][1]["take"] == 1

def test_short():
    r = consume_fefo([{"id": 1, "qty_remain": 1, "expiry": "2026-01-01"}], 5)
    assert r["ok"] is False and r["short"] == 4

def test_qty_non_positive():
    r = consume_fefo([{"id": 1, "qty_remain": 1, "expiry": "2026-01-01"}], 0)
    assert r["ok"] is False and r["reason"] == "qty_non_positive"
