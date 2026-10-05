from datetime import datetime, timedelta, timezone

from app.db import connect

def init_db():
    c = connect()
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, name TEXT, layer TEXT, unit TEXT);
    CREATE TABLE IF NOT EXISTS lots(
      id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, qty_in REAL, qty_remain REAL,
      expiry TEXT, status TEXT, data_quality TEXT
    );
    CREATE TABLE IF NOT EXISTS consumptions(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT, result_json TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    """)
    # opened-hours module columns (idempotent for existing databases)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(lots)")}
    if "opened_at" not in cols:
        c.execute("ALTER TABLE lots ADD COLUMN opened_at TEXT")
    if "open_hours" not in cols:
        c.execute("ALTER TABLE lots ADD COLUMN open_hours REAL")
    c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('warn_days','3')")
    c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('open_hours_default','48')")
    if c.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0:
        c.executemany("INSERT INTO items(name,layer,unit) VALUES (?,?,?)", [
            ("牛奶", "upper", "盒"), ("鸡蛋", "mid", "个"), ("冻饺", "lower", "袋"),
        ])
        opened_at = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        c.executemany(
            "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,opened_at,open_hours)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                (1, 2, 2, "2026-10-01", "on_shelf", "clean", None, None),
                (1, 1, 1, "2026-09-28", "on_shelf", "clean", None, None),
                (2, 12, 12, "2026-11-01", "on_shelf", "clean", opened_at, 48.0),
                (3, 1, 1, "2025-01-01", "on_shelf", "dirty", None, None),
                (2, -3, -3, "2026-12-01", "on_shelf", "dirty", None, None),
            ],
        )
    c.commit()
    c.close()
