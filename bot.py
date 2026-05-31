import os, sqlite3, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN  = os.environ.get("BOT_TOKEN",  "8912188749:AAGCVslE1Ry9kHhOMnpb7ejV_eIF6O37x4w")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://your-app.up.railway.app")
DB_PATH    = os.environ.get("DB_PATH",    "debt.db")

def get_summary():
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT COUNT(DISTINCT person_id), COALESCE(SUM(amount),0) FROM transactions").fetchone()
        conn.close(); return row[0], row[1]
    except: return 0, 0

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("📋 Qarzdorlar ro'yxatini ochish", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text("👇 Тугмани босиб реестрни очинг:", reply_markup=InlineKeyboardMarkup(keyboard))

async def cmd_jami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count, total = get_summary()
    await update.message.reply_text(
        f"📊 *Реестр хулосаси*\n\n👥 Қарздорлар: *{count} нафар*\n💰 Умумий қарз: *{int(total):,} сўм*".replace(",", " "),
        parse_mode="Markdown")

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start",   cmd_start))
    application.add_handler(CommandHandler("royhati", cmd_start))
    application.add_handler(CommandHandler("jami",    cmd_jami))
    print("✅ Bot ishga tushdi...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
