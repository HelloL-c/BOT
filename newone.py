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

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8108051087:AAFt6oxps6oWQU92Ez30lE2yhS4BesuwEFY"
MODERATOR_ID = 1068291865
POSTGRES_URL = os.environ.get("Postgres")

# Use Singapore local time
SINGAPORE_TZ = pytz.timezone("Asia/Singapore")

# Registration states
REG_COLOR, REG_ANIMAL, REG_SPORT, REG_AGE = range(4)

# We'll keep track of morning/evening automatically based on the reminder type
TARGET_COUNT = 10  # need 10 morning + 10 night

scheduler = AsyncIOScheduler()

# Form links
MORNING_FORM = "https://forms.gle/cUen9unFbdQDPtTT9"
EVENING_FORM = "https://forms.gle/wya3mQY9bPurEDU79"
CONCLUDING_FORM = "https://forms.gle/VZHUrsYSJnvyWjfq9"

# ------------------ Database Logic ------------------ #
def get_connection():
    return psycopg2.connect(POSTGRES_URL, sslmode='require')

def init_db():
    """
    Ensure user_codes table has columns for morning/night counts.
    """
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
    """
    Insert or update a user row.
    """
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

def reset_user(chat_id):
    """
    Overwrite the user's row to reset them for a brand-new diary study.
    This sets morning_count=0, night_count=0 but keeps the same color, animal, etc.
    Alternatively, you could remove the row entirely.
    """
    user = load_user(chat_id)
    if not user:
        return
    save_user(
        chat_id,
        user["color"],
        user["animal"],
        user["sport"],
        user["age"],
        user["code"],
        0,  # reset morning
        0   # reset night
    )

def update_counts(chat_id, is_morning):
    """
    Increments morning_count or night_count by 1.
    Returns (morning_count, night_count, is_done).
    """
    user = load_user(chat_id)
    if not user:
        return (0, 0, False)

    m_count = user["morning_count"]
    n_count = user["night_count"]

    if is_morning:
        m_count += 1
    else:
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
    """
    If user is in DB, show inline buttons:
    "Continue Diary Study" or "Restart Diary Study"
    If user is not in DB, begin asking color, animal, etc.
    """
    chat_id = str(update.effective_chat.id)
    user_data = load_user(chat_id)
    if user_data:
        code = user_data["code"]
        # show inline buttons
        keyboard = [
            [InlineKeyboardButton("Continue Diary Study", callback_data="cont_diary")],
            [InlineKeyboardButton("Restart Diary Study",  callback_data="restart_diary")]
        ]
        markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"Hello! You are already registered with the code: {code}.\n"
            "Would you like to continue your diary study or restart it?",
            reply_markup=markup
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "Welcome! Let’s get you registered.\nWhat's your favorite color?"
        )
        return REG_COLOR

async def handle_start_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback for the inline buttons: cont_diary or restart_diary
    If cont_diary -> do nothing
    If restart_diary -> reset counts
    """
    query = update.callback_query
    await query.answer()
    choice = query.data  # "cont_diary" or "restart_diary"
    chat_id = str(query.message.chat_id)

    if choice == "cont_diary":
        await query.edit_message_text(
            "Great! You can wait for the next reminder or type /done if you have an ongoing entry."
        )
    else:
        # "restart_diary"
        reset_user(chat_id)
        await query.edit_message_text(
            "Your diary study has been restarted! All your morning/night counts are now 0.\n"
            "You can wait for the next reminder to start submitting entries again."
        )

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

    await update.message.reply_text(
        f"Registration complete! Your code is: {code}\n"
        "We’ll send reminders at 8:00 AM and 6:00 PM local time. Stay tuned!"
    )
    return ConversationHandler.END

async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Registration canceled. Have a nice day!")
    return ConversationHandler.END

# ------------------ Reminders ------------------ #
async def morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    """At 8:00 AM local time. Sends an inline button to let user complete morning entry."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id, code FROM user_codes")
            rows = cur.fetchall()
        conn.close()

        for (chat_id, code) in rows:
            # Inline button to "Complete Morning Entry"
            keyboard = [
                [InlineKeyboardButton("Complete Morning Entry", callback_data=f"morning_{code}")]
            ]
            markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=(
                    f"Good morning!\n"
                    f"You are {code}, right?\n"
                    "Click below to fill your morning diary."
                ),
                reply_markup=markup
            )
    except Exception as e:
        logger.error(f"Error in morning reminder: {e}")
        await context.bot.send_message(chat_id=MODERATOR_ID, text=f"Error in morning reminder: {e}")

async def evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    """At 6:00 PM local time. Sends an inline button for evening entry."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id, code FROM user_codes")
            rows = cur.fetchall()
        conn.close()

        for (chat_id, code) in rows:
            keyboard = [
                [InlineKeyboardButton("Complete Evening Entry", callback_data=f"evening_{code}")]
            ]
            markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=(
                    f"Good evening!\n"
                    f"You are {code}, correct?\n"
                    "Tap below to fill your evening diary."
                ),
                reply_markup=markup
            )
    except Exception as e:
        logger.error(f"Error in evening reminder: {e}")
        await context.bot.send_message(chat_id=MODERATOR_ID, text=f"Error in evening reminder: {e}")

def schedule_jobs(application):
    if not scheduler.running:
        scheduler.start()
    # 8:00 AM local (SG time)
    scheduler.add_job(
        morning_reminder,
        CronTrigger(hour=8, minute=0, timezone=SINGAPORE_TZ),
        args=[application],
        id="morning_reminder",
        replace_existing=True
    )
    # 6:00 PM local (SG time)
    scheduler.add_job(
        evening_reminder,
        CronTrigger(hour=18, minute=0, timezone=SINGAPORE_TZ),
        args=[application],
        id="evening_reminder",
        replace_existing=True
    )

# -------------- Handling the Inline Button after Reminder -------------- #
async def reminder_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Called when user clicks "Complete Morning Entry" or "Complete Evening Entry".
    callback_data is "morning_CODE" or "evening_CODE".
    We check if code matches. If not, ask them to update or restart. 
    If yes, send form link + 'Done' button.
    """
    query = update.callback_query
    await query.answer()
    data = query.data  # e.g. "morning_BCR24" or "evening_BGR20"
    parts = data.split("_")
    if len(parts) != 2:
        await query.edit_message_text("Invalid data. Please contact support.")
        return

    entry_type, user_code = parts[0], parts[1]
    chat_id = str(query.message.chat_id)
    user = load_user(chat_id)
    if not user:
        await query.edit_message_text("You’re not registered. Please use /start.")
        return

    # Compare user_code with user["code"]
    if user_code != user["code"]:
        # Different code => inline buttons to update code or restart
        keyboard = [
            [InlineKeyboardButton("Update Code", callback_data="update_code")],
            [InlineKeyboardButton("Restart Diary", callback_data="restart_diary")]
        ]
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "It looks like your code doesn't match our records.\n"
            "Would you like to update your code or restart the diary study?",
            reply_markup=markup
        )
        return
    else:
        # Code matches => send the form link + a "Done" button
        if entry_type == "morning":
            form_link = MORNING_FORM
            friendly_text = "morning"
        else:
            form_link = EVENING_FORM
            friendly_text = "evening"

        # We'll store the entry_type in user_data so we know which count to increment on /done
        context.user_data["entry_type"] = entry_type

        keyboard = [[InlineKeyboardButton("I’m Done!", callback_data="done_entry")]]
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=(
                f"Here’s your {friendly_text} form:\n{form_link}\n"
                "Click 'I’m Done!' once you’ve submitted it."
            ),
            reply_markup=markup
        )

async def code_mismatch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback for 'update_code' or 'restart_diary' after code mismatch.
    """
    query = update.callback_query
    await query.answer()
    choice = query.data  # "update_code" or "restart_diary"
    chat_id = str(query.message.chat_id)
    user = load_user(chat_id)
    if not user:
        await query.edit_message_text("You’re not registered. Please /start.")
        return

    if choice == "update_code":
        await query.edit_message_text("Please type your new code in the chat.")
        # We can store a state or handle it with a simple approach. Let's store a state in context.
        context.user_data["awaiting_new_code"] = True
    else:
        # "restart_diary"
        reset_user(chat_id)
        await query.edit_message_text(
            "Your diary study has been restarted!\n"
            "All your morning/night counts are reset to 0.\n"
            "Use /start if you want to update your color/animal/sport/age."
        )

async def handle_new_code_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    If context.user_data["awaiting_new_code"] is True, we set the new code in DB.
    Then ask user to re-click the reminder or wait for next reminder.
    """
    if not context.user_data.get("awaiting_new_code", False):
        return  # Not waiting for a code update, ignore

    new_code = update.message.text.strip()
    chat_id = str(update.effective_chat.id)
    user = load_user(chat_id)
    if not user:
        await update.message.reply_text("You’re not registered. Please /start.")
        context.user_data["awaiting_new_code"] = False
        return

    # Keep existing color, animal, etc., just update code
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
    await update.message.reply_text(
        f"Your code is updated to {new_code}.\n"
        "Please wait for the next reminder or press the inline button if it was already sent."
    )
    context.user_data["awaiting_new_code"] = False

async def done_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    If user clicks "I’m Done!" inline button => increment morning/night count
    """
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    user = load_user(chat_id)
    if not user:
        await query.edit_message_text("You’re not registered. Please /start.")
        return

    entry_type = context.user_data.get("entry_type", None)
    if entry_type not in ["morning", "evening"]:
        await query.edit_message_text("No entry type found. Please wait for next reminder.")
        return

    is_morning = (entry_type == "morning")
    m_count, n_count, is_done = update_counts(chat_id, is_morning)

    if is_done:
        await query.edit_message_text(
            text=(
                f"Fantastic! You’ve completed {m_count} morning and {n_count} evening entries.\n"
                f"Here’s the concluding form:\n{CONCLUDING_FORM}\n"
                "You can end here or /start if you wish to do it all again!"
            )
        )
    else:
        remain_m = max(0, TARGET_COUNT - m_count)
        remain_n = max(0, TARGET_COUNT - n_count)
        if is_morning:
            e_word = "morning"
        else:
            e_word = "evening"

        await query.edit_message_text(
            text=(
                f"Your {e_word} entry is recorded.\n"
                f"You have {remain_m} morning and {remain_n} evening entries left to reach {TARGET_COUNT}."
            )
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

    # Inline callback for continuing or restarting the diary
    application.add_handler(CallbackQueryHandler(handle_start_buttons, pattern="^(cont_diary|restart_diary)$"))

    # Schedule jobs
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

    # Callback for "Complete Morning Entry" or "Complete Evening Entry"
    application.add_handler(CallbackQueryHandler(reminder_button_handler, pattern="^(morning_|evening_)"))

    # Callback for code mismatch => update code or restart diary
    application.add_handler(CallbackQueryHandler(code_mismatch_handler, pattern="^(update_code|restart_diary)$"))

    # Message handler for user typing a new code after mismatch
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_code_message))

    # Inline "I’m Done!" => increment count
    application.add_handler(CallbackQueryHandler(done_button_handler, pattern="^done_entry$"))

    application.run_polling()

if __name__ == "__main__":
    main()
