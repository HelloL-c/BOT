import logging
import os
import psycopg2
import pytz
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Define your timezone
SINGAPORE_TZ = pytz.timezone("Asia/Singapore")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8108051087:AAFt6oxps6oWQU92Ez30lE2yhS4BesuwEFY"
MODERATOR_ID = 1068291865
POSTGRES_URL = os.environ.get("Postgres")

# Registration states
REG_COLOR, REG_ANIMAL, REG_SPORT, REG_AGE = range(4)

# Reminder conversation states
CHECK_CODE, CODE_CHOICE, DIARY_CHOICE = range(10, 13)

scheduler = AsyncIOScheduler()

# Form links
MORNING_FORM = "https://forms.gle/cUen9unFbdQDPtTT9"
EVENING_FORM = "https://forms.gle/wya3mQY9bPurEDU79"
CONCLUDING_FORM = "https://forms.gle/VZHUrsYSJnvyWjfq9"

TARGET_COUNT = 10  # Both morning_count and night_count must reach 10

# ------------------ Database Logic ------------------ #
def get_connection():
    return psycopg2.connect(POSTGRES_URL, sslmode='require')

def init_db():
    """Create or modify the user_codes table to include morning/night counts."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_codes (
                chat_id TEXT PRIMARY KEY,
                color   TEXT,
                animal  TEXT,
                sport   TEXT,
                age     TEXT,
                code    TEXT,
                morning_count INT DEFAULT 0,
                night_count   INT DEFAULT 0
            );
        """)
        conn.commit()
    conn.close()

def load_user(chat_id):
    """Load a single user row from DB."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT color, animal, sport, age, code, morning_count, night_count
            FROM user_codes
            WHERE chat_id=%s
        """, (chat_id,))
        row = cur.fetchone()
    conn.close()
    if row:
        color, animal, sport, age, code, m_count, n_count = row
        return {
            "color": color,
            "animal": animal,
            "sport": sport,
            "age": age,
            "code": code,
            "morning_count": m_count,
            "night_count": n_count
        }
    return None

def save_user(chat_id, color, animal, sport, age, code, morning_count=0, night_count=0):
    """Insert or update a user row."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO user_codes (chat_id, color, animal, sport, age, code, morning_count, night_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE
              SET color=EXCLUDED.color,
                  animal=EXCLUDED.animal,
                  sport=EXCLUDED.sport,
                  age=EXCLUDED.age,
                  code=EXCLUDED.code,
                  morning_count=EXCLUDED.morning_count,
                  night_count=EXCLUDED.night_count
        """, (chat_id, color, animal, sport, age, code, morning_count, night_count))
        conn.commit()
    conn.close()

def update_counts(chat_id, entry_type):
    """
    Increments morning_count or night_count by 1.
    Returns (morning_count, night_count, is_done).
    is_done = True if both >= TARGET_COUNT.
    """
    user = load_user(chat_id)
    if not user:
        return (0, 0, False)

    m_count = user["morning_count"]
    n_count = user["night_count"]

    if entry_type == "morning":
        m_count += 1
    elif entry_type == "night":
        n_count += 1

    save_user(
        chat_id,
        user["color"],
        user["animal"],
        user["sport"],
        user["age"],
        user["code"],
        m_count,
        n_count
    )
    is_done = (m_count >= TARGET_COUNT and n_count >= TARGET_COUNT)
    return (m_count, n_count, is_done)

# ------------------ Registration Flow ------------------ #
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

async def reg_animal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    animal = update.message.text.strip()
    if not animal:
        await update.message.reply_text("Please enter a valid animal.")
        return REG_ANIMAL
    context.user_data["animal"] = animal
    await update.message.reply_text("Awesome! What's your favorite sport?")
    return REG_SPORT

async def reg_sport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sport = update.message.text.strip()
    if not sport:
        await update.message.reply_text("Please enter a valid sport.")
        return REG_SPORT
    context.user_data["sport"] = sport
    await update.message.reply_text("Cool! Lastly, what's your age?")
    return REG_AGE

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
    # Initialize morning/night counts to 0
    save_user(chat_id, color, animal, sport, age_str, code, 0, 0)

    await update.message.reply_text(f"Registration complete! Your code is: {code}")
    return ConversationHandler.END

async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Registration canceled.")
    return ConversationHandler.END

# ------------------ Reminder Flow ------------------ #
async def morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    """
    Called by APScheduler at local 8:00 AM.
    Tells user to type /reminder for morning entry check.
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id, code FROM user_codes")
            rows = cur.fetchall()
        conn.close()

        for (chat_id, code) in rows:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=(
                    f"Good morning!\n"
                    f"If you are {code}, type /reminder to proceed.\n"
                    f"If not, type /reminder anyway to update your code or restart."
                )
            )
    except Exception as e:
        logger.error(f"Error in morning reminder: {e}")
        await context.bot.send_message(chat_id=MODERATOR_ID, text=f"Error in morning reminder: {e}")

async def evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    """
    Called by APScheduler at local 6:00 PM.
    Tells user to type /reminder for evening entry check.
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id, code FROM user_codes")
            rows = cur.fetchall()
        conn.close()

        for (chat_id, code) in rows:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=(
                    f"Good evening!\n"
                    f"If you are {code}, type /reminder to proceed.\n"
                    f"If not, type /reminder anyway to update your code or restart."
                )
            )
    except Exception as e:
        logger.error(f"Error in evening reminder: {e}")
        await context.bot.send_message(chat_id=MODERATOR_ID, text=f"Error in evening reminder: {e}")

def schedule_jobs(application):
    if not scheduler.running:
        scheduler.start()
    # 8:00 AM local time (Singapore)
    scheduler.add_job(
        morning_reminder, 
        CronTrigger(hour=8, minute=0, timezone=SINGAPORE_TZ),
        args=[application],
        id="morning_reminder",
        replace_existing=True
    )
    # 6:00 PM local time (Singapore)
    scheduler.add_job(
        evening_reminder, 
        CronTrigger(hour=18, minute=0, timezone=SINGAPORE_TZ),
        args=[application],
        id="evening_reminder",
        replace_existing=True
    )

# Conversation states for the reminder flow
CHECK_CODE, CODE_CHOICE, DIARY_CHOICE = range(10,13)

async def reminder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reminder command. Check if user is in DB, ask them "Are you CODE? Yes or No"
    """
    chat_id = str(update.effective_chat.id)
    user = load_user(chat_id)
    if not user:
        await update.message.reply_text("You are not registered. Please /start to register.")
        return ConversationHandler.END

    code = user["code"]
    keyboard = [
        [InlineKeyboardButton("Yes, that's me", callback_data="yes_code")],
        [InlineKeyboardButton("No, that's not me", callback_data="no_code")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Are you {code}?", reply_markup=markup)
    return CHECK_CODE

async def reminder_check_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback after user clicks yes_code/no_code
    """
    query = update.callback_query
    await query.answer()
    choice = query.data  # "yes_code" or "no_code"
    chat_id = str(query.message.chat_id)
    user = load_user(chat_id)

    if choice == "yes_code":
        # Ask if morning or night
        keyboard = [
            [InlineKeyboardButton("Morning Entry", callback_data="morning")],
            [InlineKeyboardButton("Night Entry", callback_data="night")]
        ]
        await query.edit_message_text("Is this a Morning or Night entry?", reply_markup=InlineKeyboardMarkup(keyboard))
        return DIARY_CHOICE
    else:
        # no_code
        keyboard = [
            [InlineKeyboardButton("Update Code", callback_data="update_code")],
            [InlineKeyboardButton("Restart Registration", callback_data="restart_reg")]
        ]
        await query.edit_message_text("Do you want to update your code or restart registration?", reply_markup=InlineKeyboardMarkup(keyboard))
        return CODE_CHOICE

async def reminder_code_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback for update_code or restart_reg
    """
    query = update.callback_query
    await query.answer()
    choice = query.data
    chat_id = str(query.message.chat_id)

    if choice == "update_code":
        await query.edit_message_text("Please type your new code.")
        return CODE_CHOICE  # We'll handle text input for new code in a message handler
    else:
        # "restart_reg"
        await query.edit_message_text("Please use /start to re-register.")
        return ConversationHandler.END

async def reminder_update_code_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    The user typed their new code. Save it, then ask morning or night.
    """
    chat_id = str(update.effective_chat.id)
    new_code = update.message.text.strip()
    user = load_user(chat_id)
    if not user:
        await update.message.reply_text("You are not registered. Please /start.")
        return ConversationHandler.END

    # Keep existing counts
    save_user(
        chat_id,
        user["color"],
        user["animal"],
        user["sport"],
        user["age"],
        new_code,
        user["morning_count"],
        user["night_count"]
    )
    await update.message.reply_text(f"Your code is updated to {new_code}. Morning or night entry? (Type 'morning' or 'night')")
    return DIARY_CHOICE

async def reminder_diary_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    The user types 'morning' or 'night'. Send link. Wait for /done to increment counts.
    """
    chat_id = str(update.effective_chat.id)
    user = load_user(chat_id)
    if not user:
        await update.message.reply_text("You are not registered. Please /start.")
        return ConversationHandler.END

    entry_type = update.message.text.strip().lower()
    if entry_type not in ["morning", "night"]:
        await update.message.reply_text("Please type 'morning' or 'night'.")
        return DIARY_CHOICE

    if entry_type == "morning":
        await update.message.reply_text(f"Please fill this morning form:\n{MORNING_FORM}\nType /done when finished.")
    else:
        await update.message.reply_text(f"Please fill this evening form:\n{EVENING_FORM}\nType /done when finished.")

    context.user_data["entry_type"] = entry_type
    return ConversationHandler.END

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    User typed /done after finishing the form. Increment count, check if both are 10. 
    If so, concluding link is shown, else show countdown.
    """
    chat_id = str(update.effective_chat.id)
    user = load_user(chat_id)
    if not user:
        await update.message.reply_text("You are not registered. Please /start.")
        return

    entry_type = context.user_data.get("entry_type", None)
    if entry_type not in ["morning", "night"]:
        await update.message.reply_text("No entry type found. Please do /reminder first.")
        return

    # Increment counts
    m_count, n_count, is_done = update_counts(chat_id, entry_type)
    if is_done:
        await update.message.reply_text(
            f"Congratulations! You've completed {m_count} morning and {n_count} night entries.\n"
            f"Here is the concluding form: {CONCLUDING_FORM}\n"
            f"You can stop now or do it all over again if you wish."
        )
    else:
        remain_m = max(0, TARGET_COUNT - m_count)
        remain_n = max(0, TARGET_COUNT - n_count)
        await update.message.reply_text(
            f"Your {entry_type} entry has been recorded.\n"
            f"You have {remain_m} morning and {remain_n} night entries left to reach {TARGET_COUNT}."
        )

def main():
    init_db()
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Registration conversation
    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start_registration)],
        states={
            REG_COLOR:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_color)],
            REG_ANIMAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_animal)],
            REG_SPORT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_sport)],
            REG_AGE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_age)],
        },
        fallbacks=[CommandHandler("cancel", reg_cancel)],
    )
    application.add_handler(reg_conv)

    # Reminder conversation
    reminder_conv = ConversationHandler(
        entry_points=[CommandHandler("reminder", reminder_start)],
        states={
            CHECK_CODE: [
                CallbackQueryHandler(reminder_check_code, pattern="^(yes_code|no_code)$"),
            ],
            CODE_CHOICE: [
                CallbackQueryHandler(reminder_code_choice, pattern="^(update_code|restart_reg)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_update_code_text)
            ],
            DIARY_CHOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_diary_choice)
            ],
        },
        fallbacks=[CommandHandler("cancel", reg_cancel)],
    )
    application.add_handler(reminder_conv)

    # /done command to finalize entry
    application.add_handler(CommandHandler("done", done_command))

    # Schedule reminders (local SG time)
    if not scheduler.running:
        scheduler.start()
    scheduler.add_job(
        morning_reminder, 
        CronTrigger(hour=8, minute=0, timezone=SINGAPORE_TZ),
        args=[application],
        id="morning_reminder",
        replace_existing=True
    )
    scheduler.add_job(
        evening_reminder, 
        CronTrigger(hour=18, minute=0, timezone=SINGAPORE_TZ),
        args=[application],
        id="evening_reminder",
        replace_existing=True
    )

    application.run_polling()

if __name__ == "__main__":
    main()
