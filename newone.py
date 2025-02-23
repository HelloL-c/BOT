import logging
import os
import psycopg2
import pytz
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
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

#############################
# Basic Configuration
#############################

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8108051087:AAFt6oxps6oWQU92Ez30lE2yhS4BesuwEFY"
MODERATOR_ID = 1068291865
ADMIN_ID = 1068291865  # Replace with your actual admin chat ID
POSTGRES_URL = os.environ.get("Postgres")

SINGAPORE_TZ = pytz.timezone("Asia/Singapore")

# Registration states
REG_COLOR, REG_ANIMAL, REG_SPORT, REG_AGE = range(4)

TARGET_COUNT = 10  # need 10 morning + 10 night

scheduler = AsyncIOScheduler()

MORNING_FORM = "https://forms.gle/cUen9unFbdQDPtTT9"
EVENING_FORM = "https://forms.gle/wya3mQY9bPurEDU79"
CONCLUDING_FORM = "https://forms.gle/VZHUrsYSJnvyWjfq9"

last_reminder_counts = {
    "morning": {},
    "evening": {}
}

#############################
# Database Logic
#############################

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
                code    TEXT,
                morning_count INT DEFAULT 0 NOT NULL,
                night_count   INT DEFAULT 0 NOT NULL
            );
        """)
        conn.commit()
    conn.close()

def fix_db():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE user_codes SET morning_count=0 WHERE morning_count IS NULL;")
        cur.execute("UPDATE user_codes SET night_count=0 WHERE night_count IS NULL;")
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
            "chat_id": chat_id,
            "color": color,
            "animal": animal,
            "sport": sport,
            "age": age,
            "code": code,
            "morning_count": m_count or 0,
            "night_count": n_count or 0
        }
    return None

def load_user_by_code(codename):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT chat_id, color, animal, sport, age, code, morning_count, night_count
            FROM user_codes
            WHERE code=%s
        """, (codename,))
        row = cur.fetchone()
    conn.close()
    if row:
        chat_id, color, animal, sport, age, code, m_count, n_count = row
        return {
            "chat_id": chat_id,
            "color": color,
            "animal": animal,
            "sport": sport,
            "age": age,
            "code": code,
            "morning_count": m_count or 0,
            "night_count": n_count or 0
        }
    return None

def get_all_users():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT chat_id, color, animal, sport, age, code, morning_count, night_count
            FROM user_codes
        """)
        rows = cur.fetchall()
    conn.close()
    users = []
    for row in rows:
        chat_id, color, animal, sport, age, code, m_count, n_count = row
        users.append({
            "chat_id": chat_id,
            "color": color,
            "animal": animal,
            "sport": sport,
            "age": age,
            "code": code,
            "morning_count": m_count or 0,
            "night_count": n_count or 0
        })
    return users

def save_user(chat_id, color, animal, sport, age, code, morning_count=0, night_count=0):
    morning_count = morning_count or 0
    night_count = night_count or 0
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
        0,
        0
    )

def update_user_code(chat_id, new_code):
    user = load_user(chat_id)
    if user:
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

def update_counts(chat_id, is_morning):
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

#############################
# Registration Flow
#############################

REG_COLOR, REG_ANIMAL, REG_SPORT, REG_AGE = range(4)

async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user_data = load_user(chat_id)

    if user_data and user_data["color"] != "":
        code = user_data["code"]
        keyboard = [
            [InlineKeyboardButton("Continue Diary Study", callback_data="cont_diary")],
            [InlineKeyboardButton("Restart Diary Study", callback_data="restart_diary")],
            [InlineKeyboardButton("Change Codename", callback_data="change_code_start")]
        ]
        markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"Hello! You are already registered with the code: {code}.\n"
            "Would you like to continue, restart, or change your code?",
            reply_markup=markup
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "Welcome! Let’s get you registered.\nWhat's your favorite color?"
        )
        return REG_COLOR

async def handle_start_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    chat_id = str(query.message.chat_id)

    if choice == "cont_diary":
        await query.edit_message_text(
            "Great! You can wait for the next reminder or type /done if you have an ongoing entry."
        )
    elif choice == "restart_diary":
        reset_user(chat_id)
        await query.edit_message_text(
            "Your diary study has been restarted! All your morning/night counts are now 0.\n"
            "You can wait for the next reminder to start submitting entries again."
        )
    elif choice == "change_code_start":
        await query.edit_message_text(
            "Please type your new code in the chat."
        )
        context.user_data["change_code_from_start"] = True

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
    save_user(chat_id, color, animal, sport, age_str, code, 0, 0)

    await update.message.reply_text(
        f"Registration complete! Your code is: {code}\n"
        "We’ll send reminders at 8:00 AM and 6:00 PM local time. Stay tuned!"
    )
    return ConversationHandler.END

async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Registration canceled. Have a nice day!")
    return ConversationHandler.END

#############################
# Reminders & Storing Counts
#############################

def store_current_counts(period):
    ...

async def morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    ...

async def evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    ...

def schedule_jobs(application):
    ...

#############################
# Participant -> Admin typed message flow
#############################

async def reminder_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

async def code_mismatch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

async def done_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

async def participant_to_admin_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

#############################
# Admin
#############################

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

async def admin_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

async def show_all_users_progress(query):
    ...

async def show_inline_all_codes(query, prefix):
    ...

def check_forgot_entries():
    ...

#############################
# Admin: inline code selection
#############################
async def admin_code_inline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

#############################
# Admin: "adm_reset_" or "adm_change_"
#############################
async def admin_reset_change_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

#############################
# Admin: Broadcasting & Private
#############################

async def broadcast_to_all(message: str, context: ContextTypes.DEFAULT_TYPE):
    ...

async def private_message_user(chat_id: str, message: str, context: ContextTypes.DEFAULT_TYPE):
    ...

#############################
# Admin: Checking Who Forgot
#############################
def check_forgot_entries():
    ...

#############################
# Admin: Test Reminders
#############################
async def test_morning_reminder_for_admin(context: ContextTypes.DEFAULT_TYPE):
    ...

async def test_evening_reminder_for_admin(context: ContextTypes.DEFAULT_TYPE):
    ...

#############################
# Single Text Handler
#############################

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

#############################
# Admin Confirmation Callbacks
#############################

async def admin_broadcast_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

async def admin_private_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

#############################
# Next Reminders Info
#############################

def get_next_reminders_info():
    ...

#############################
# Main
#############################

def main():
    init_db()
    # fix_db()  # if needed

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

    # Start/restart/change code
    application.add_handler(CallbackQueryHandler(handle_start_buttons, 
        pattern="^(cont_diary|restart_diary|change_code_start)$"))

    schedule_jobs(application)

    # Reminders
    application.add_handler(CallbackQueryHandler(reminder_button_handler, 
        pattern="^(morning_|evening_|contactadmin_)"))
    application.add_handler(CallbackQueryHandler(code_mismatch_handler, 
        pattern="^(update_code|restart_diary)$"))
    application.add_handler(CallbackQueryHandler(done_button_handler, 
        pattern="^done_entry$"))

    # Participant->Admin confirm typed message
    application.add_handler(CallbackQueryHandler(participant_to_admin_confirm_callback, 
        pattern="^(p2a_confirm_yes|p2a_confirm_no)$"))

    # Admin
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(admin_menu_handler, 
        pattern="^(adm_check_progress|adm_find_code|adm_reset_change|adm_forgot|adm_broadcast|adm_private|adm_testall|adm_next_reminders)$"))

    # "adm_find_BCR25", "adm_reset_change_BCR25", "adm_private_BCR25"
    application.add_handler(CallbackQueryHandler(admin_code_inline_handler, 
        pattern="^adm_"))

    # "adm_reset_BCR25" or "adm_change_BCR25"
    application.add_handler(CallbackQueryHandler(admin_reset_change_callback, 
        pattern="^(adm_reset_|adm_change_)"))

    # Confirm broadcast
    application.add_handler(CallbackQueryHandler(admin_broadcast_confirm_callback, 
        pattern="^(adm_broadcast_confirm_yes|adm_broadcast_confirm_no)$"))

    # Confirm private message
    application.add_handler(CallbackQueryHandler(admin_private_confirm_callback, 
        pattern="^(adm_private_confirm_yes|adm_private_confirm_no)$"))

    # Single text handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    application.run_polling()

if __name__ == "__main__":
    main()
