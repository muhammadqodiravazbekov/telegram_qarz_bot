import hmac
import hashlib
import json
from urllib.parse import parse_qsl
from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import sqlite3, os, httpx

app = FastAPI()

# CORS sozlamalari
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

DB_PATH       = os.environ.get("DB_PATH", "debt.db")
BOT_TOKEN     = os.environ.get("BOT_TOKEN", "8912188749:AAGCVslE1Ry9kHhOMnpb7ejV_eIF6O37x4w") # Render-da config var qilib qo'yish tavsiya etiladi
WEBAPP_URL    = os.environ.get("WEBAPP_URL", "https://telegram-qarz-bot.onrender.com")
ALLOWED_GROUP = int(os.environ.get("ALLOWED_GROUP", "-1003618616072"))
TG            = f"https://api.telegram.org/bot{BOT_TOKEN}"

def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init():
    c = db()
    c.execute("""
        CREATE TABLE IF NOT EXISTS persons(
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            name TEXT UNIQUE NOT NULL, 
            created_at TEXT DEFAULT(datetime('now'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            person_id INTEGER NOT NULL, 
            amount REAL NOT NULL, 
            note TEXT, 
            added_by TEXT, 
            created_at TEXT DEFAULT(datetime('now')), 
            FOREIGN KEY(person_id) REFERENCES persons(id) ON DELETE CASCADE
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sellers(
            tg_id INTEGER PRIMARY KEY, 
            name TEXT, 
            is_active INTEGER DEFAULT 1
        )
    """)
    c.commit()
    c.close()

init()

def verify_telegram_webapp(init_data: str) -> dict:
    """Telegram WebApp yuborgan ma'lumotlarning haqiqiyligini tekshirish"""
    if not init_data:
        raise HTTPException(status_code=401, detail="Ruxsat berilmagan (InitData mavjud emas)")
    try:
        vals = dict(parse_qsl(init_data))
        hash_val = vals.pop("hash", None)
        if not hash_val:
            raise HTTPException(status_code=401, detail="Ruxsat berilmagan (Hash mavjud emas)")
        
        data_check_string = "\n".join([f"{k}={v}" for k, v in sorted(vals.items())])
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != hash_val:
            raise HTTPException(status_code=401, detail="Xavfsizlik xatosi (Ma'lumotlar soxta)")
        
        return json.loads(vals.get("user", "{}"))
    except Exception:
        raise HTTPException(status_code=401, detail="Validatsiya jarayonida xatolik")

def get_current_seller(x_tg_init_data: Optional[str] = Header(None)):
    """Faqat tasdiqlangan sotuvchilarga ruxsat beruvchi xavfsizlik filtri"""
    user_data = verify_telegram_webapp(x_tg_init_data)
    tg_id = user_data.get("id")
    
    if not tg_id:
        raise HTTPException(status_code=403, detail="Foydalanuvchi ID topilmadi")
        
    c = db()
    seller = c.execute("SELECT * FROM sellers WHERE tg_id = ? AND is_active = 1", (tg_id,)).fetchone()
    c.close()
    
    if not seller:
        raise HTTPException(status_code=403, detail="Siz sotuvchi emassiz! Ma'lumotlarni o'zgartirish taqiqlanadi.")
    
    return {"tg_id": tg_id, "name": user_data.get("first_name", "Sotuvchi")}

async def tgsend(chat_id, payload, thread_id=None):
    if thread_id: 
        payload["message_thread_id"] = thread_id
    async with httpx.AsyncClient() as cl:
        await cl.post(f"{TG}/sendMessage", json=payload)

@app.post("/bot")
async def webhook(req: Request):
    try: 
        data = await req.json()
    except: 
        return {"ok": True}
    
    msg = data.get("message") or data.get("channel_post")
    if not msg: 
        return {"ok": True}
    
    cid = msg.get("chat", {}).get("id")
    txt = msg.get("text", "").split("@")[0].strip()
    tid = msg.get("message_thread_id")
    user_id = msg.get("from", {}).get("id")
    first_name = msg.get("from", {}).get("first_name", "Sotuvchi")
    
    if cid != ALLOWED_GROUP: 
        return {"ok": True}
    
    # Guruhda sotuvchilarni ro'yxatga olish buyrug'i
    if txt == "/seller_init" and user_id:
        c = db()
        c.execute("INSERT OR REPLACE INTO sellers (tg_id, name, is_active) VALUES (?, ?, 1)", (user_id, first_name))
        c.commit()
        c.close()
        await tgsend(cid, {"chat_id": cid, "text": f"✅ {first_name} muvaffaqiyatli sotuvchi sifatida ro'yxatga olindi!"}, tid)
        return {"ok": True}

    if txt in ("/start", "/royhati"):
        await tgsend(cid, {
            "chat_id": cid, 
            "text": "👇 Quyidagi tugma orqali qarzdorlar ro'yxatiga kiring:", 
            "reply_markup": {"inline_keyboard": [[{"text": "📋 Qarzdorlar dasturi", "web_app":{"url": WEBAPP_URL}}]]}
        }, tid)
    elif txt == "/jami":
        c = db()
        rows = c.execute("SELECT p.name, COALESCE(SUM(t.amount),0) as tot, GROUP_CONCAT(t.amount,'|') as bd FROM persons p LEFT JOIN transactions t ON p.id=t.person_id GROUP BY p.id HAVING tot>0 ORDER BY tot DESC LIMIT 30").fetchall()
        grand = c.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE amount>0").fetchone()[0]
        c.close()
        lines = []
        for r in rows:
            parts = [str(int(float(x))) for x in (r["bd"] or "").split("|") if x and float(x) > 0]
            bd = " + ".join(parts[:6]) + ("…" if len(parts) > 6 else "")
            lines.append(f"• {r['name']}: {bd} = *{int(r['tot']):,}*".replace(",", "  "))
        await tgsend(cid, {"chat_id": cid, "text": "📑 *Qarzdorlar*\n\n" + "\n".join(lines) + f"\n\n💰 *Jami: {int(grand):,} so'm*", "parse_mode": "Markdown"}, tid)
    return {"ok": True}

class PC(BaseModel):
    name: str

class TC(BaseModel):
    person_id: int
    amount: float
    note: Optional[str] = None
    added_by: Optional[str] = "Sotuvchi"

# === KO'RISH AMALLARI (HAMMA UCHUN OCHIQ) ===
@app.get("/api/persons")
def lp():
    c = db()
    rows = c.execute("SELECT p.id, p.name, COUNT(t.id) AS tx_count, COALESCE(SUM(t.amount),0) AS total, GROUP_CONCAT(t.amount,'|') AS bd FROM persons p LEFT JOIN transactions t ON p.id=t.person_id GROUP BY p.id ORDER BY total DESC").fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        bd = d.pop("bd") or ""
        d["breakdown"] = [float(x) for x in bd.split("|") if x]
        out.append(d)
    return out

@app.get("/api/persons/{pid}/transactions")
def gt(pid: int):
    c = db()
    rows = c.execute("SELECT * FROM transactions WHERE person_id=? ORDER BY created_at DESC", (pid,)).fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.get("/api/stats")
def stats():
    c = db()
    total = c.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE amount>0").fetchone()[0]
    paid = c.execute("SELECT COUNT(DISTINCT person_id) FROM(SELECT person_id, SUM(amount) as s FROM transactions GROUP BY person_id HAVING s<=0)").fetchone()[0]
    active = c.execute("SELECT COUNT(DISTINCT person_id) FROM(SELECT person_id, SUM(amount) as s FROM transactions GROUP BY person_id HAVING s>0)").fetchone()[0]
    top = c.execute("SELECT p.name, SUM(t.amount) as tot FROM persons p JOIN transactions t ON p.id=t.person_id GROUP BY p.id ORDER BY tot DESC LIMIT 3").fetchall()
    c.close()
    return {"total": total, "paid": paid, "active": active, "top": [dict(r) for r in top]}


# === O'ZGARTIRISH AMALLARI (FAQAT SOTUVCHILAR UCHUN - GET_CURRENT_SELLER FILTRI ORQALI) ===
@app.post("/api/persons", status_code=201)
def cp(b: PC, seller: dict = Depends(get_current_seller)):
    name = b.name.strip()
    if not name: 
        raise HTTPException(400, "Ism bo'sh bo'lishi mumkin emas")
    c = db()
    try:
        cur = c.execute("INSERT INTO persons(name) VALUES(?)", (name,))
        c.commit()
        pid = cur.lastrowid
    except sqlite3.IntegrityError:
        c.close()
        raise HTTPException(400, "Bu ismdagi shaxs allaqachon mavjud")
    c.close()
    return {"id": pid, "name": name}

@app.delete("/api/persons/{pid}")
def dp(pid: int, seller: dict = Depends(get_current_seller)):
    c = db()
    c.execute("DELETE FROM transactions WHERE person_id=?", (pid,))
    c.execute("DELETE FROM persons WHERE id=?", (pid,))
    c.commit()
    c.close()
    return {"ok": True}

@app.post("/api/transactions", status_code=201)
def at(b: TC, seller: dict = Depends(get_current_seller)):
    c = db()
    if not c.execute("SELECT 1 FROM persons WHERE id=?", (b.person_id,)).fetchone():
        c.close()
        raise HTTPException(404, "Shaxs topilmadi")
    cur = c.execute("INSERT INTO transactions(person_id, amount, note, added_by) VALUES(?,?,?,?)", (b.person_id, b.amount, b.note, seller['name']))
    c.commit()
    tid = cur.lastrowid
    c.close()
    return {"id": tid}

@app.delete("/api/transactions/{tid}")
def dt(tid: int, seller: dict = Depends(get_current_seller)):
    c = db()
    c.execute("DELETE FROM transactions WHERE id=?", (tid,))
    c.commit()
    c.close()
    return {"ok": True}

# Static fayllar uchun joylashuv (Frontend fayllaringiz "static" papkasida bo'lishi lozim)
if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
