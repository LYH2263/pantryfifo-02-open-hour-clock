from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import lotstore, seed
from app.db import connect
from app.lotstore import LotError

app = FastAPI(title="Pantryfifo", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "pantryfifo"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

@app.get("/api/fridge")
def fridge(layer: str | None = None):
    return lotstore.list_fridge(layer)

@app.get("/api/alerts")
def alerts():
    return lotstore.list_alerts()

class LotIn(BaseModel):
    item_id: int
    qty: float
    expiry: str

@app.post("/api/lots")
def inbound(body: LotIn):
    c = connect()
    item = c.execute("SELECT id FROM items WHERE id=?", (body.item_id,)).fetchone()
    if not item: c.close(); raise HTTPException(404, "item")
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
        (body.item_id, body.qty, body.qty, body.expiry, "on_shelf", "clean"))
    c.commit(); lid = cur.lastrowid; c.close(); return {"id": lid}

@app.get("/api/lots/{lot_id}/open-preview")
def open_preview(lot_id: int):
    try:
        return lotstore.open_preview(lot_id)
    except LotError as e:
        raise HTTPException(e.status, e.detail)

@app.post("/api/lots/{lot_id}/open")
def open_lot(lot_id: int):
    try:
        return lotstore.open_confirm(lot_id)
    except LotError as e:
        raise HTTPException(e.status, e.detail)

class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    note: str = ""

@app.post("/api/consume")
def consume(body: ConsumeIn):
    try:
        return lotstore.consume(body.item_id, body.qty, body.note)
    except LotError as e:
        raise HTTPException(e.status, e.detail)

@app.post("/api/expire-sweep")
def expire_sweep():
    return {"expired_ids": lotstore.sweep()}

@app.get("/api/settings")
def settings():
    return lotstore.get_settings()

class SettingsIn(BaseModel):
    warn_days: int | None = None
    open_hours_default: float | None = None

@app.put("/api/settings")
def put_settings(body: SettingsIn):
    try:
        return lotstore.update_settings(body.model_dump(exclude_none=True))
    except LotError as e:
        raise HTTPException(e.status, e.detail)
