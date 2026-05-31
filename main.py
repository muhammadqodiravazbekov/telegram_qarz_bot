from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import sqlite3, os, httpx, re

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH       = os.environ.get("DB_PATH","debt.db")
BOT_TOKEN     = os.environ.get("BOT_TOKEN","8912188749:AAGCVslE1Ry9kHhOMnpb7ejV_eIF6O37x4w")
WEBAPP_URL    = os.environ.get("WEBAPP_URL","https://telegram-qarz-bot.onrender.com")
ALLOWED_GROUP = int(os.environ.get("ALLOWED_GROUP","-1003618616072"))
TG            = f"https://api.telegram.org/bot{BOT_TOKEN}"

def db():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; return c

def init():
    c=db()
    c.execute("CREATE TABLE IF NOT EXISTS persons(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL,created_at TEXT DEFAULT(datetime('now')))")
    c.execute("CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,person_id INTEGER NOT NULL,amount REAL NOT NULL,note TEXT,added_by TEXT,created_at TEXT DEFAULT(datetime('now')),FOREIGN KEY(person_id)REFERENCES persons(id)ON DELETE CASCADE)")
    c.commit(); c.close()
init()

async def tgsend(chat_id,payload,thread_id=None):
    if thread_id: payload["message_thread_id"]=thread_id
    async with httpx.AsyncClient() as cl: await cl.post(f"{TG}/sendMessage",json=payload)

@app.get("/bot")
async def bget(): return {"ok":True}

@app.post("/bot")
async def webhook(req: Request):
    try: data=await req.json()
    except: return {"ok":True}
    msg=data.get("message") or data.get("channel_post")
    if not msg: return {"ok":True}
    cid=msg.get("chat",{}).get("id")
    txt=msg.get("text","").split("@")[0].strip()
    tid=msg.get("message_thread_id")
    if cid!=ALLOWED_GROUP: return {"ok":True}
    
    if txt in("/start","/royhati"):
        await tgsend(cid,{"chat_id":cid,"text":"📋 Quyidagi tugma orqali dasturni oching:","reply_markup":{"inline_keyboard":[[{"text":"💻 Dasturni ochish","web_app":{"url":WEBAPP_URL}}]]}},tid)
    elif txt=="/jami":
        c=db()
        rows=c.execute("SELECT p.name,COALESCE(SUM(t.amount),0)as tot,GROUP_CONCAT(t.amount,'|')as bd FROM persons p LEFT JOIN transactions t ON p.id=t.person_id GROUP BY p.id HAVING tot>0 ORDER BY tot DESC LIMIT 30").fetchall()
        grand=c.execute("SELECT COALESCE(SUM(amount),0)FROM transactions WHERE amount>0").fetchone()[0] or 0
        c.close()
        lines=[]
        for r in rows:
            parts=[str(int(float(x)))for x in(r["bd"]or"").split("|")if x and float(x)>0]
            bd=" + ".join(parts[:6])+("…" if len(parts)>6 else "")
            lines.append(f"• {r['name']}: {bd} = *{int(r['tot']):,}*".replace(",","  "))
        await tgsend(cid,{"chat_id":cid,"text":"📑 *Qarzdorlar Ro'yxati*\n\n"+"\n".join(lines)+f"\n\n💰 *Jami qarz: {int(grand):,} so'm*".replace(",","  "),"parse_mode":"Markdown"},tid)
    return {"ok":True}

class PC(BaseModel): name:str
class TC(BaseModel): person_id:int; amount:float; note:Optional[str]=None; added_by:Optional[str]="Sotuvchi"
class TU(BaseModel): note: str

@app.get("/api/persons")
def lp():
    c=db()
    rows=c.execute("SELECT p.id,p.name,COUNT(t.id)AS tx_count,COALESCE(SUM(t.amount),0)AS total,GROUP_CONCAT(t.amount,'|')AS bd FROM persons p LEFT JOIN transactions t ON p.id=t.person_id GROUP BY p.id ORDER BY total DESC").fetchall()
    c.close()
    out=[]
    for r in rows:
        d=dict(r); bd=d.pop("bd") or ""
        d["breakdown"]=[float(x)for x in bd.split("|")if x]
        out.append(d)
    return out

@app.post("/api/persons",status_code=201)
def cp(b:PC):
    name=b.name.strip()
    if not name: raise HTTPException(400,"Ism bo'sh")
    c=db()
    try: cur=c.execute("INSERT INTO persons(name)VALUES(?)",(name,)); c.commit(); pid=cur.lastrowid
    except sqlite3.IntegrityError: c.close(); raise HTTPException(400,"Bu ism mavjud")
    c.close(); return{"id":pid,"name":name}

@app.delete("/api/persons/{pid}")
def dp(pid:int):
    c=db(); c.execute("DELETE FROM transactions WHERE person_id=?",(pid,)); c.execute("DELETE FROM persons WHERE id=?",(pid,)); c.commit(); c.close(); return{"ok":True}

@app.get("/api/persons/{pid}/transactions")
def gt(pid:int):
    c=db(); rows=c.execute("SELECT * FROM transactions WHERE person_id=? ORDER BY created_at DESC",(pid,)).fetchall(); c.close(); return[dict(r)for r in rows]

@app.post("/api/transactions",status_code=201)
def at(b:TC):
    c=db()
    if not c.execute("SELECT 1 FROM persons WHERE id=?",(b.person_id,)).fetchone():
        c.close(); raise HTTPException(404,"Topilmadi")
    cur=c.execute("INSERT INTO transactions(person_id,amount,note,added_by)VALUES(?,?,?,?)",(b.person_id,b.amount,b.note,b.added_by)); c.commit(); tid=cur.lastrowid; c.close(); return{"id":tid}

@app.put("/api/transactions/{tid}")
def ut(tid:int, b:TU):
    c=db(); c.execute("UPDATE transactions SET note=? WHERE id=?",(b.note, tid)); c.commit(); c.close(); return{"ok":True}

@app.delete("/api/transactions/{tid}")
def dt(tid:int):
    c=db(); c.execute("DELETE FROM transactions WHERE id=?",(tid,)); c.commit(); c.close(); return{"ok":True}

@app.get("/api/stats")
def stats():
    c=db()
    total=c.execute("SELECT COALESCE(SUM(amount),0)FROM transactions WHERE amount>0").fetchone()[0] or 0
    paid=c.execute("SELECT COUNT(DISTINCT person_id)FROM(SELECT person_id,SUM(amount)as s FROM transactions GROUP BY person_id HAVING s<=0)").fetchone()[0] or 0
    active=c.execute("SELECT COUNT(DISTINCT person_id)FROM(SELECT person_id,SUM(amount)as s FROM transactions GROUP BY person_id HAVING s>0)").fetchone()[0] or 0
    top=c.execute("SELECT p.name,SUM(t.amount)as tot FROM persons p JOIN transactions t ON p.id=t.person_id GROUP BY p.id ORDER BY tot DESC LIMIT 3").fetchall()
    c.close()
    return{"total":total,"paid":paid,"active":active,"top":[dict(r)for r in top]}

app.mount("/",StaticFiles(directory="static",html=True),name="static")
