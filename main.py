from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3, os, httpx

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH    = os.environ.get("DB_PATH", "debt.db")
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "8912188749:AAGCVslE1Ry9kHhOMnpb7ejV_eIF6O37x4w")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://telegram-qarz-bot.onrender.com")
TG_API     = f"https://api.telegram.org/bot{BOT_TOKEN}"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("CREATE TABLE IF NOT EXISTS persons (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, created_at TEXT DEFAULT (datetime('now')))")
    conn.execute("CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER NOT NULL, amount REAL NOT NULL, note TEXT, added_by TEXT, created_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE)")
    conn.commit(); conn.close()

init_db()

@app.get("/bot")
async def bot_get():
    return JSONResponse({"ok": True})

@app.post("/bot")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except:
        return JSONResponse({"ok": True})
    
    message = data.get("message") or data.get("channel_post")
    if not message:
        return JSONResponse({"ok": True})
    
    chat_id = message.get("chat", {}).get("id")
    text    = message.get("text", "")

    if text in ("/start", "/royhati"):
        await send_button(chat_id)
    elif text == "/jami":
        await send_jami(chat_id)

    return JSONResponse({"ok": True})

async def send_button(chat_id):
    async with httpx.AsyncClient() as client:
        await client.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": "👇 Тугмани босиб реестрни очинг:",
            "reply_markup": {"inline_keyboard": [[{"text": "📋 Qarzdorlar ro'yxatini ochish", "web_app": {"url": WEBAPP_URL}}]]}
        })

async def send_jami(chat_id):
    conn = get_db()
    row = conn.execute("SELECT COUNT(DISTINCT person_id), COALESCE(SUM(amount),0) FROM transactions").fetchone()
    conn.close()
    count, total = row[0], int(row[1])
    async with httpx.AsyncClient() as client:
        await client.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": f"📊 *Реестр хулосаси*\n\n👥 Қарздорлар: *{count} нафар*\n💰 Умумий қарз: *{total:,} сўм*".replace(",", " "),
            "parse_mode": "Markdown"
        })

class PersonCreate(BaseModel):
    name: str

class TransactionCreate(BaseModel):
    person_id: int
    amount: float
    note: Optional[str] = None
    added_by: Optional[str] = "Номаълум"

@app.get("/api/persons")
def list_persons():
    conn = get_db()
    rows = conn.execute("SELECT p.id, p.name, COUNT(t.id) AS tx_count, COALESCE(SUM(t.amount),0) AS total FROM persons p LEFT JOIN transactions t ON p.id=t.person_id GROUP BY p.id ORDER BY total DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/persons", status_code=201)
def create_person(body: PersonCreate):
    name = body.name.strip()
    if not name: raise HTTPException(400, "Исм бўш")
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO persons (name) VALUES (?)", (name,))
        conn.commit(); pid = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close(); raise HTTPException(400, "Бу исм мавжуд")
    conn.close()
    return {"id": pid, "name": name}

@app.delete("/api/persons/{person_id}")
def delete_person(person_id: int):
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE person_id=?", (person_id,))
    conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
    conn.commit(); conn.close()
    return {"ok": True}

@app.get("/api/persons/{person_id}/transactions")
def get_transactions(person_id: int):
    conn = get_db()
    rows = conn.execute("SELECT * FROM transactions WHERE person_id=? ORDER BY created_at DESC", (person_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/transactions", status_code=201)
def add_transaction(body: TransactionCreate):
    if body.amount <= 0: raise HTTPException(400, "Сумма 0 дан катта")
    conn = get_db()
    if not conn.execute("SELECT 1 FROM persons WHERE id=?", (body.person_id,)).fetchone():
        conn.close(); raise HTTPException(404, "Шахс топилмади")
    cur = conn.execute("INSERT INTO transactions (person_id,amount,note,added_by) VALUES (?,?,?,?)", (body.person_id, body.amount, body.note, body.added_by))
    conn.commit(); tid = cur.lastrowid; conn.close()
    return {"id": tid}

@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: int):
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
    conn.commit(); conn.close()
    return {"ok": True}

app.mount("/", StaticFiles(directory="static", html=True), name="static")
