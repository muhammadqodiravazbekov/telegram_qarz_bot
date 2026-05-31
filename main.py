from fastapi import FastAPI, HTTPException, Request, Header
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
ALLOWED_GROUP = int(os.environ.get("ALLOWED_GROUP", "-1003618616072"))
TG_API        = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── DB ────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS persons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        created_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        note TEXT,
        added_by TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE)""")
    conn.commit(); conn.close()

init_db()

# ── Telegram auth validation ──────────────────────────────
def validate_tg_data(init_data: str) -> dict | None:
    try:
        parsed = dict(x.split('=', 1) for x in unquote(init_data).split('&'))
        check_hash = parsed.pop('hash', '')
        data_check = '\n'.join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, check_hash):
            return None
        user = json.loads(parsed.get('user', '{}'))
        return user
    except:
        return None

async def is_group_member(user_id: int) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{TG_API}/getChatMember", params={"chat_id": ALLOWED_GROUP, "user_id": user_id})
            data = r.json()
            status = data.get("result", {}).get("status", "")
            return status in ("member", "administrator", "creator")
    except:
        return False

def auth_error():
    return JSONResponse({"error": "Ruxsat yo'q"}, status_code=403)

# ── Bot webhook ───────────────────────────────────────────
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

    chat_id   = message.get("chat", {}).get("id")
    chat_type = message.get("chat", {}).get("type", "")
    text      = message.get("text", "").split("@")[0]  # handle /start@botname
    thread_id = message.get("message_thread_id")

    if text not in ("/start", "/royhati", "/jami"):
        return JSONResponse({"ok": True})

    # allow private chat from group members OR the group itself
    if chat_id != ALLOWED_GROUP and chat_type != "private":
        return JSONResponse({"ok": True})

    if text in ("/start", "/royhati"):
        await send_button(chat_id, thread_id)
    elif text == "/jami":
        await send_jami(chat_id, thread_id)

    return JSONResponse({"ok": True})

async def tg_send(chat_id, payload, thread_id=None):
    if thread_id:
        payload["message_thread_id"] = thread_id
    async with httpx.AsyncClient() as client:
        await client.post(f"{TG_API}/sendMessage", json=payload)

async def send_button(chat_id, thread_id=None):
    await tg_send(chat_id, {
        "chat_id": chat_id,
        "text": "👇 Tugmani bosib ro'yxatni oching:",
        "reply_markup": {"inline_keyboard": [[{
            "text": "📋 Qarzdorlar ro'yxati",
            "web_app": {"url": WEBAPP_URL}
        }]]}
    }, thread_id)

async def send_jami(chat_id, thread_id=None):
    conn = get_db()
    persons = conn.execute("""
        SELECT p.name, COALESCE(SUM(t.amount),0) as total,
               GROUP_CONCAT(t.amount, '+') as breakdown
        FROM persons p
        LEFT JOIN transactions t ON p.id=t.person_id
        GROUP BY p.id HAVING total > 0
        ORDER BY total DESC LIMIT 20
    """).fetchall()
    grand = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions").fetchone()[0]
    conn.close()

    lines = []
    for p in persons:
        breakdown = p["breakdown"] or ""
        # show breakdown if multiple transactions
        parts = [x for x in breakdown.split(',') if x and float(x) > 0]
        if len(parts) > 1:
            bd = " + ".join(str(int(float(x))) for x in parts[:5])
            if len(parts) > 5:
                bd += f" +…({len(parts)} ta)"
            lines.append(f"• {p['name']}: {bd} = *{int(p['total']):,}*".replace(",", " "))
        else:
            lines.append(f"• {p['name']}: *{int(p['total']):,}*".replace(",", " "))

    text = f"📊 *Qarzdorlar ro'yxati*\n\n" + "\n".join(lines)
    text += f"\n\n💰 *Jami: {int(grand):,} so'm*".replace(",", " ")

    await tg_send(chat_id, {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }, thread_id)

# ── API ───────────────────────────────────────────────────
class PersonCreate(BaseModel):
    name: str
    init_data: Optional[str] = None

class TransactionCreate(BaseModel):
    person_id: int
    amount: float
    note: Optional[str] = None
    added_by: Optional[str] = "Sotuvchi"
    init_data: Optional[str] = None

@app.get("/api/persons")
async def list_persons(x_init_data: Optional[str] = Header(None)):
    conn = get_db()
    rows = conn.execute("""
        SELECT p.id, p.name, COUNT(t.id) AS tx_count,
               COALESCE(SUM(t.amount),0) AS total,
               GROUP_CONCAT(t.amount, '|') as breakdown
        FROM persons p
        LEFT JOIN transactions t ON p.id=t.person_id
        GROUP BY p.id ORDER BY total DESC
    """).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        bd = d.pop("breakdown") or ""
        parts = [float(x) for x in bd.split("|") if x and float(x) > 0]
        d["breakdown"] = parts
        result.append(d)
    return result

@app.post("/api/persons", status_code=201)
async def create_person(body: PersonCreate):
    name = body.name.strip()
    if not name: raise HTTPException(400, "Ism bo'sh")
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO persons (name) VALUES (?)", (name,))
        conn.commit(); pid = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close(); raise HTTPException(400, "Bu ism mavjud")
    conn.close()
    return {"id": pid, "name": name}

@app.delete("/api/persons/{person_id}")
async def delete_person(person_id: int):
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE person_id=?", (person_id,))
    conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
    conn.commit(); conn.close()
    return {"ok": True}

@app.get("/api/persons/{person_id}/transactions")
async def get_transactions(person_id: int):
    conn = get_db()
    rows = conn.execute("SELECT * FROM transactions WHERE person_id=? ORDER BY created_at DESC", (person_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/transactions", status_code=201)
async def add_transaction(body: TransactionCreate):
    conn = get_db()
    if not conn.execute("SELECT 1 FROM persons WHERE id=?", (body.person_id,)).fetchone():
        conn.close(); raise HTTPException(404, "Shaxs topilmadi")
    cur = conn.execute("INSERT INTO transactions (person_id,amount,note,added_by) VALUES (?,?,?,?)",
                       (body.person_id, body.amount, body.note, body.added_by))
    conn.commit(); tid = cur.lastrowid; conn.close()
    return {"id": tid}

@app.delete("/api/transactions/{tx_id}")
async def delete_transaction(tx_id: int):
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
    conn.commit(); conn.close()
    return {"ok": True}

app.mount("/", StaticFiles(directory="static", html=True), name="static")
