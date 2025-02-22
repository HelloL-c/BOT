import logging
import os
import psycopg2
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8108051087:AAFt6oxps6oWQU92Ez30lE2yhS4BesuwEFY"
MODERATOR_ID = 1068291865
POSTGRES_URL = os.environ.get("Postgres")

# Registration states
REG_COLOR, REG_ANIMAL, REG_SPORT, REG_AGE = range(4)

scheduler = AsyncIOScheduler()

def get_connection():
    return psycopg2.connect(POSTGRES_URL, sslmode='require')

def init_db():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_codes (
                chat_id TEXT PRIMARY KEY,
                color   TEXT,
                animal  TEXT,
                sport   TEXT,
                age     TEXT,
                code    TEXT
            );
        """)
        conn.commit()
    conn.close()

def load_user(chat_id):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT color, animal, sport, age, code FROM user_codes WHERE chat_id=%s", (chat_id,))
        row = cur.fetchone()
    conn.close()
    if row:
        color, animal, sport, age, code = row
        return {"color": color, "animal": animal, "sport": sport, "age": age, "code": code}
    return None

def save_user(chat_id, color, animal, sport, age, code):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO user_codes (chat_id, color, animal, sport, age, code)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE
              SET color=EXCLUDED.color,
                  animal=EXCLUDED.animal,
                  sport=EXCLUDED.sport,
                  age=EXCLUDED.age,
                  code=EXCLUDED.code
        """, (chat_id, color, animal, sport, age, code))
        conn.commit()
    conn.close()

async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user_data = load_user(chat_id)
    if user_data:
        code = user_data["code"]
        await update.message.reply_text(f"You are already registered. Your anonymous code is: {code}")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Welcome! Let's get you registered.\nWhat's your favorite color?")
        return REG_COLOR

async def reg_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
    color = update.message.text.strip()
    if not color:
        await update.message.reply_text("Please enter a valid color.")
        return REG_COLOR
    context.user_data["color"] = color
    await update.message.reply_text("Great! What's your favorite animal?")
    return REG_ANIMAL

# ... (Same pattern for reg_animal, reg_sport) ...

async def reg_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    age_str = update.message.text.strip()
    if not age_str.isdigit():
        await update.message.reply_text("Please enter a valid number for your age:")
        return REG_AGE

    color = context.user_data["color"]
    animal = context.user_data["animal"]
    sport = context.user_data["sport"]

    code = f"{color[0].upper()}{animal[0].upper()}{sport[0].upper()}{age_str}"
    save_user(chat_id, color, animal, sport, age_str, code)

    await update.message.reply_text(f"Registration complete! Your code is: {code}")
    return ConversationHandler.END

async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Registration canceled.")
    return ConversationHandler.END

async def send_morning_reminder(context):
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id FROM user_codes")
            rows = cur.fetchall()
        conn.close()

        for (chat_id,) in rows:
            await context.bot.send_message(chat_id=int(chat_id), text="Good morning! Please complete your morning diary.")
    except Exception as e:
        logger.error(f"Error in morning reminder: {e}")
        await context.bot.send_message(chat_id=MODERATOR_ID, text=f"Error in morning reminder: {e}")

async def send_evening_reminder(context):
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id FROM user_codes")
            rows = cur.fetchall()
        conn.close()

        for (chat_id,) in rows:
            await context.bot.send_message(chat_id=int(chat_id), text="Good evening! Please complete your evening diary.")
    except Exception as e:
        logger.error(f"Error in evening reminder: {e}")
        await context.bot.send_message(chat_id=MODERATOR_ID, text=f"Error in evening reminder: {e}")

def schedule_jobs(application):
    if not scheduler.running:
        scheduler.start()
    scheduler.add_job(send_morning_reminder, CronTrigger(hour=8, minute=0), args=[application], id="morning_reminder", replace_existing=True)
    scheduler.add_job(send_evening_reminder, CronTrigger(hour=20, minute=0), args=[application], id="evening_reminder", replace_existing=True)

def main():
    init_db()  # Create table if not exists
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_registration)],
        states={
            REG_COLOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_color)],
            REG_ANIMAL: [...],  # same pattern
            REG_SPORT: [...],
            REG_AGE: [...]
        },
        fallbacks=[CommandHandler("cancel", reg_cancel)],
    )
    application.add_handler(conv_handler)

    schedule_jobs(application)
    application.run_polling()

if __name__ == "__main__":
    main()
