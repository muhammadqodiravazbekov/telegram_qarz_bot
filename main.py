from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3, os, httpx, hashlib, hmac, json
from urllib.parse import unquote

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH       = os.environ.get("DB_PATH", "debt.db")
BOT_TOKEN     = os.environ.get("BOT_TOKEN", "8912188749:AAGCVslE1Ry9kHhOMnpb7ejV_eIF6O37x4w")
WEBAPP_URL    = os.environ.get("WEBAPP_URL", "https://telegram-qarz-bot.onrender.com")
ALLOWED_GROUP = int(os.environ.get("ALLOWED_GROUP", "-1001234567890") or 0)
# Comma-separated Telegram user IDs of authorized sellers
# Example: ALLOWED_USERS=123456789,987654321
ALLOWED_USERS = {x.strip() for x in os.environ.get("ALLOWED_USERS", "6657685041").split(",") if x.strip()}
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── DB ──────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            added_by TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE);
    """)
    conn.commit(); conn.close()

init_db()

# ── Auth ─────────────────────────────────────────────────────────────────────
def validate_tg_data(init_data: str) -> dict | None:
    try:
        parsed = dict(x.split('=', 1) for x in unquote(init_data).split('&'))
        check_hash = parsed.pop('hash', '')
        data_check = '\n'.join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, check_hash):
            return None
        return json.loads(parsed.get('user', '{}'))
    except:
        return None

async def require_seller(x_init_data: Optional[str] = Header(None)):
    """FastAPI dependency — blocks non-sellers from mutating endpoints."""
    if not x_init_data:
        raise HTTPException(403, "Auth kerak")
    user = validate_tg_data(x_init_data)
    if not user:
        raise HTTPException(403, "Yaroqsiz token")
    if ALLOWED_USERS and str(user.get("id", "")) not in ALLOWED_USERS:
        raise HTTPException(403, "Ruxsat yo'q — faqat sotuvchilar")
    return user

# ── /api/me — role check ─────────────────────────────────────────────────────
@app.get("/api/me")
async def get_me(x_init_data: Optional[str] = Header(None)):
    if not x_init_data:
        return {"role": "viewer", "name": "Mehmon"}
    user = validate_tg_data(x_init_data) or {}
    uid = str(user.get("id", ""))
    is_seller = not ALLOWED_USERS or uid in ALLOWED_USERS
    return {
        "role": "seller" if is_seller else "viewer",
        "name": user.get("first_name", "Foydalanuvchi"),
        "uid": uid
    }

# ── Models ────────────────────────────────────────────────────────────────────
class PersonCreate(BaseModel):
    name: str

class TxCreate(BaseModel):
    person_id: int
    amount: float
    note: Optional[str] = None
    added_by: Optional[str] = "Sotuvchi"

# ── Read endpoints (public within the app) ───────────────────────────────────
@app.get("/api/persons")
def list_persons():
    conn = get_db()
    rows = conn.execute("""
        SELECT p.id, p.name, COUNT(t.id) tx_count,
               COALESCE(SUM(t.amount), 0) total
        FROM persons p LEFT JOIN transactions t ON p.id = t.person_id
        GROUP BY p.id ORDER BY total DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/persons/{pid}/transactions")
def get_transactions(pid: int):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE person_id=? ORDER BY created_at DESC", (pid,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/stats")
def get_stats():
    conn = get_db()
    total   = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions").fetchone()[0]
    count   = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
    tx_cnt  = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    conn.close()
    return {"total": total, "persons": count, "transactions": tx_cnt}

# ── Write endpoints (sellers only) ──────────────────────────────────────────
@app.post("/api/persons", status_code=201)
async def create_person(body: PersonCreate, user=Depends(require_seller)):
    name = body.name.strip()
    if not name: raise HTTPException(400, "Ism bo'sh")
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO persons (name) VALUES (?)", (name,))
        conn.commit(); pid = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close(); raise HTTPException(400, "Bu ism allaqachon mavjud")
    conn.close()
    return {"id": pid, "name": name}

@app.delete("/api/persons/{pid}")
async def delete_person(pid: int, user=Depends(require_seller)):
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE person_id=?", (pid,))
    conn.execute("DELETE FROM persons WHERE id=?", (pid,))
    conn.commit(); conn.close()
    return {"ok": True}

@app.post("/api/transactions", status_code=201)
async def add_transaction(body: TxCreate, user=Depends(require_seller)):
    if body.amount <= 0: raise HTTPException(400, "Summa 0 dan katta bo'lsin")
    conn = get_db()
    if not conn.execute("SELECT 1 FROM persons WHERE id=?", (body.person_id,)).fetchone():
        conn.close(); raise HTTPException(404, "Shaxs topilmadi")
    added_by = user.get("first_name", body.added_by or "Sotuvchi")
    cur = conn.execute(
        "INSERT INTO transactions (person_id,amount,note,added_by) VALUES (?,?,?,?)",
        (body.person_id, body.amount, body.note, added_by)
    )
    conn.commit(); tid = cur.lastrowid; conn.close()
    return {"id": tid}

@app.delete("/api/transactions/{tid}")
async def delete_transaction(tid: int, user=Depends(require_seller)):
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE id=?", (tid,))
    conn.commit(); conn.close()
    return {"ok": True}

@app.patch("/api/persons/{pid}/name")
async def rename_person(pid: int, body: PersonCreate, user=Depends(require_seller)):
    name = body.name.strip()
    if not name: raise HTTPException(400, "Ism bo'sh")
    conn = get_db()
    try:
        conn.execute("UPDATE persons SET name=? WHERE id=?", (name, pid))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close(); raise HTTPException(400, "Bu ism allaqachon mavjud")
    conn.close()
    return {"ok": True}

# ── Telegram Bot Webhook ─────────────────────────────────────────────────────
@app.post("/bot")
async def bot_webhook(request: Request):
    try: data = await request.json()
    except: return JSONResponse({"ok": True})
    msg = data.get("message") or data.get("channel_post")
    if not msg: return JSONResponse({"ok": True})
    chat_id   = msg.get("chat", {}).get("id")
    text      = (msg.get("text", "") or "").split("@")[0]
    thread_id = msg.get("message_thread_id")
    if text in ("/start", "/royhati"):
        await _tg_send(chat_id, {
            "chat_id": chat_id,
            "text": "📋 Qarzdorlar ro'yxatini ko'rish:",
            "reply_markup": {"inline_keyboard": [[
                {"text": "📋 Ro'yxatni ochish", "web_app": {"url": WEBAPP_URL}}
            ]]}
        }, thread_id)
    elif text == "/jami":
        await _send_jami(chat_id, thread_id)
    return JSONResponse({"ok": True})

async def _tg_send(chat_id, payload, thread_id=None):
    if thread_id: payload["message_thread_id"] = thread_id
    async with httpx.AsyncClient() as c:
        await c.post(f"{TG_API}/sendMessage", json=payload)

async def _send_jami(chat_id, thread_id=None):
    conn = get_db()
    total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions").fetchone()[0]
    count = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
    top5  = conn.execute("""
        SELECT p.name, COALESCE(SUM(t.amount),0) s FROM persons p
        LEFT JOIN transactions t ON p.id=t.person_id
        GROUP BY p.id ORDER BY s DESC LIMIT 5
    """).fetchall()
    conn.close()
    lines = "\n".join(f"• {r['name']}: *{int(r['s']):,}*".replace(",", " ") for r in top5)
    await _tg_send(chat_id, {
        "chat_id": chat_id,
        "text": f"📊 *Umumiy holat*\n\n👥 {count} ta qarzdor\n💰 Jami: *{int(total):,} so'm*\n\n🔝 Top 5:\n{lines}".replace(",", " "),
        "parse_mode": "Markdown"
    }, thread_id)

app.mount("/", StaticFiles(directory="static", html=True), name="static")
