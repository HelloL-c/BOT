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

# We track morning/evening automatically based on the reminder type
TARGET_COUNT = 10  # need 10 morning + 10 night

scheduler = AsyncIOScheduler()

# Google Forms
MORNING_FORM = "https://forms.gle/cUen9unFbdQDPtTT9"
EVENING_FORM = "https://forms.gle/wya3mQY9bPurEDU79"
CONCLUDING_FORM = "https://forms.gle/VZHUrsYSJnvyWjfq9"

# For checking who forgot an entry after 1 hour
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
    """
    Ensure user_codes table has columns for morning/night counts with default 0.
    Also sets them to NOT NULL so they can't be NULL in the future.
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
        # Make sure columns are NOT NULL and have default 0
        cur.execute("""
            ALTER TABLE user_codes
            ALTER COLUMN morning_count SET DEFAULT 0,
            ALTER COLUMN morning_count SET NOT NULL,
            ALTER COLUMN night_count SET DEFAULT 0,
            ALTER COLUMN night_count SET NOT NULL;
        """)
        conn.commit()
    conn.close()

def fix_db():
    """
    Optional function to update existing rows that might still have NULL 
    in morning_count or night_count. Run once if you have old data.
    """
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE user_codes SET morning_count=0 WHERE morning_count IS NULL;")
        cur.execute("UPDATE user_codes SET night_count=0   WHERE night_count   IS NULL;")
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
            # Convert any None to 0, just in case
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
    # Ensure morning_count, night_count are never None
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
    if user_data:
        code = user_data["code"]
        keyboard = [
            [InlineKeyboardButton("Continue Diary Study", callback_data="cont_diary")],
            [InlineKeyboardButton("Restart Diary Study", callback_data="restart_diary")]
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
    query = update.callback_query
    await query.answer()
    choice = query.data
    chat_id = str(query.message.chat_id)

    if choice == "cont_diary":
        await query.edit_message_text(
            "Great! You can wait for the next reminder or type /done if you have an ongoing entry."
        )
    else:
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
    # Initialize counts to 0
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
    """Store the current morning/night counts for each user so we can see who forgot after 1 hour."""
    all_users = get_all_users()
    if period == "morning":
        last_reminder_counts["morning"] = {u["chat_id"]: u["morning_count"] for u in all_users}
    else:
        last_reminder_counts["evening"] = {u["chat_id"]: u["night_count"] for u in all_users}

async def morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    try:
        all_users = get_all_users()
        for u in all_users:
            chat_id = u["chat_id"]
            code = u["code"]
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
        store_current_counts("morning")
    except Exception as e:
        logger.error(f"Error in morning reminder: {e}")
        await context.bot.send_message(chat_id=MODERATOR_ID, text=f"Error in morning reminder: {e}")

async def evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    try:
        all_users = get_all_users()
        for u in all_users:
            chat_id = u["chat_id"]
            code = u["code"]
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
        store_current_counts("evening")
    except Exception as e:
        logger.error(f"Error in evening reminder: {e}")
        await context.bot.send_message(chat_id=MODERATOR_ID, text=f"Error in evening reminder: {e}")

def schedule_jobs(application):
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

#############################
# Reminder Buttons
#############################

async def reminder_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("_")
    if len(parts) != 2:
        await query.edit_message_text("Invalid data. Please contact support.")
        return

    entry_type, user_code = parts
    chat_id = str(query.message.chat_id)
    user = load_user(chat_id)
    if not user:
        await query.edit_message_text("You’re not registered. Please use /start.")
        return

    if user_code != user["code"]:
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
        if entry_type == "morning":
            form_link = MORNING_FORM
            friendly_text = "morning"
            context.user_data["entry_type"] = "morning"
        else:
            form_link = EVENING_FORM
            friendly_text = "evening"
            context.user_data["entry_type"] = "evening"

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
    query = update.callback_query
    await query.answer()
    choice = query.data
    chat_id = str(query.message.chat_id)
    user = load_user(chat_id)
    if not user:
        await query.edit_message_text("You’re not registered. Please /start.")
        return

    if choice == "update_code":
        await query.edit_message_text("Please type your new code in the chat.")
        context.user_data["awaiting_new_code"] = True
    else:
        reset_user(chat_id)
        await query.edit_message_text(
            "Your diary study has been restarted!\n"
            "All your morning/night counts are reset to 0.\n"
            "Use /start if you want to update your color/animal/sport/age."
        )

async def handle_new_code_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_new_code", False):
        return
    new_code = update.message.text.strip()
    chat_id = str(update.effective_chat.id)
    user = load_user(chat_id)
    if not user:
        await update.message.reply_text("You’re not registered. Please /start.")
        context.user_data["awaiting_new_code"] = False
        return

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
        e_word = "morning" if is_morning else "evening"
        await query.edit_message_text(
            text=(
                f"Your {e_word} entry is recorded.\n"
                f"You have {remain_m} morning and {remain_n} evening entries left to reach {TARGET_COUNT}."
            )
        )

#############################
# Admin-Only Functionality
#############################

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /admin command. Only admin_id can use. """
    if update.effective_chat.id != ADMIN_ID:
        await update.message.reply_text("Sorry, you’re not authorized.")
        return

    keyboard = [
        [InlineKeyboardButton("Check user progress", callback_data="adm_check_progress")],
        [InlineKeyboardButton("Find user by codename", callback_data="adm_find_code")],
        [InlineKeyboardButton("Reset/Change codename", callback_data="adm_reset_change")],
        [InlineKeyboardButton("Who forgot entry (1h past)?", callback_data="adm_forgot")],
        [InlineKeyboardButton("Broadcast Message", callback_data="adm_broadcast")],
        [InlineKeyboardButton("Private Message a Participant", callback_data="adm_private")],
        [InlineKeyboardButton("Test All Bot Functions", callback_data="adm_testall")],
        [InlineKeyboardButton("Check Next Reminders", callback_data="adm_next_reminders")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Hello Admin! What do you need?", reply_markup=markup)

async def admin_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.message.chat_id != ADMIN_ID:
        await query.edit_message_text("Not authorized.")
        return

    choice = query.data
    if choice == "adm_check_progress":
        await show_all_users_progress(query)
    elif choice == "adm_find_code":
        # Show inline list of codenames
        await show_inline_all_codes(query, prefix="adm_find_")
    elif choice == "adm_reset_change":
        # Show inline list of codenames
        await show_inline_all_codes(query, prefix="adm_reset_change_")
    elif choice == "adm_forgot":
        text = check_forgot_entries()
        await query.edit_message_text(text or "No missing entries found.")
    elif choice == "adm_broadcast":
        await query.edit_message_text("Please type the message to broadcast to all participants.")
        context.user_data["adm_broadcast"] = True
    elif choice == "adm_private":
        # Show inline list of codenames
        await show_inline_all_codes(query, prefix="adm_private_")
    elif choice == "adm_testall":
        await query.edit_message_text("Sending test morning & evening reminders to admin only with real inline forms...")
        await test_morning_reminder_for_admin(context)
        await test_evening_reminder_for_admin(context)
        await query.message.reply_text("Test complete! These test reminders were NOT sent to participants.")
    elif choice == "adm_next_reminders":
        text = get_next_reminders_info()
        await query.edit_message_text(text)

async def show_all_users_progress(query):
    users = get_all_users()
    if not users:
        await query.edit_message_text("No users found.")
        return
    lines = []
    for u in users:
        m_count = u["morning_count"] or 0
        n_count = u["night_count"] or 0
        remain_m = max(0, TARGET_COUNT - m_count)
        remain_n = max(0, TARGET_COUNT - n_count)
        lines.append(
            f"Code: {u['code']}, M:{m_count}/{TARGET_COUNT} "
            f"(left {remain_m}), E:{n_count}/{TARGET_COUNT} "
            f"(left {remain_n})"
        )
    await query.edit_message_text("\n".join(lines) or "No data.")

async def show_inline_all_codes(query, prefix):
    """
    Lists all user codenames as inline buttons.
    prefix might be "adm_find_" or "adm_reset_change_" or "adm_private_"
    """
    users = get_all_users()
    if not users:
        await query.edit_message_text("No users found.")
        return

    buttons = []
    row = []
    for u in users:
        code = u["code"]
        row.append(InlineKeyboardButton(code, callback_data=f"{prefix}{code}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text("Please select a codename:", reply_markup=markup)

def check_forgot_entries():
    missing = []
    all_users = get_all_users()

    old_morning = last_reminder_counts.get("morning", {})
    for u in all_users:
        if u["chat_id"] in old_morning:
            old_val = old_morning[u["chat_id"]]
            if (u["morning_count"] or 0) == (old_val or 0):
                missing.append(f"{u['code']} forgot morning entry")

    old_evening = last_reminder_counts.get("evening", {})
    for u in all_users:
        if u["chat_id"] in old_evening:
            old_val = old_evening[u["chat_id"]]
            if (u["night_count"] or 0) == (old_val or 0):
                missing.append(f"{u['code']} forgot evening entry")

    return "\n".join(missing)

#############################
# Admin: inline code selection
#############################

async def admin_code_inline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles e.g. "adm_find_BCR25", "adm_reset_change_BCR25", "adm_private_BCR25"
    """
    query = update.callback_query
    await query.answer()
    data = query.data
    # data might be "adm_find_BCR25", "adm_reset_change_BCR25", "adm_private_BCR25"
    prefix, codename = data.split("_", 1)  # e.g. prefix="adm_find", codename="BCR25"

    if prefix == "adm_find":
        # Show user info
        user = load_user_by_code(codename)
        if user:
            m_count = user["morning_count"] or 0
            n_count = user["night_count"] or 0
            remain_m = max(0, TARGET_COUNT - m_count)
            remain_n = max(0, TARGET_COUNT - n_count)
            msg = (
                f"Code: {codename}\n"
                f"Morning: {m_count} (left {remain_m})\n"
                f"Evening: {n_count} (left {remain_n})\n"
                f"ChatID: {user['chat_id']}\n"
                f"Color/Animal/Sport/Age: {user['color']}, {user['animal']}, {user['sport']}, {user['age']}"
            )
            await query.edit_message_text(msg)
        else:
            await query.edit_message_text(f"No user found with code {codename}.")

    elif prefix == "adm_reset":
        # Actually we might do "adm_reset_change_BCR25"? 
        # but let's do a simpler approach. We'll handle subcases below
        pass
    elif prefix == "adm_reset_change":
        # Show sub-menu
        user = load_user_by_code(codename)
        if not user:
            await query.edit_message_text(f"No user found with code {codename}.")
            return
        kb = [
            [InlineKeyboardButton("Reset counts", callback_data=f"adm_reset_{codename}")],
            [InlineKeyboardButton("Change code", callback_data=f"adm_change_{codename}")]
        ]
        markup = InlineKeyboardMarkup(kb)
        await query.edit_message_text(
            f"User found: {codename}.\nDo you want to reset counts or change code?",
            reply_markup=markup
        )

    elif prefix == "adm_private":
        user = load_user_by_code(codename)
        if not user:
            await query.edit_message_text(f"No user found with code {codename}.")
            return
        context.user_data["adm_private_chatid"] = user["chat_id"]
        context.user_data["adm_private_msg"] = True
        await query.edit_message_text(f"Please type the message you want to send to {codename}.")

#############################
# Admin: "adm_reset_" or "adm_change_"
#############################

async def admin_reset_change_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data  # e.g. "adm_reset_BCR24" or "adm_change_BCR24"
    parts = data.split("_", 2)
    # => ["adm", "reset", "BCR24"] or ["adm", "change", "BCR24"]
    if len(parts) != 3:
        await query.edit_message_text("Invalid data.")
        return
    action = parts[1]  # "reset" or "change"
    codename = parts[2]

    if action == "reset":
        user = load_user_by_code(codename)
        if not user:
            await query.edit_message_text("User not found.")
            return
        reset_user(user["chat_id"])
        await query.edit_message_text(f"User with code {codename} has been reset to 0 morning/evening counts.")
    elif action == "change":
        user = load_user_by_code(codename)
        if not user:
            await query.edit_message_text("User not found.")
            return
        context.user_data["adm_change_user"] = user["chat_id"]
        await query.edit_message_text("Please type the new code you want to assign.")
        context.user_data["adm_changing_code"] = True

async def admin_change_code_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("adm_changing_code"):
        return
    new_code = update.message.text.strip()
    chat_id = context.user_data["adm_change_user"]
    user = load_user(chat_id)
    if not user:
        await update.message.reply_text("User not found or no longer exists.")
        context.user_data["adm_changing_code"] = False
        return
    update_user_code(chat_id, new_code)
    await update.message.reply_text(f"User code updated to {new_code}.")
    context.user_data["adm_changing_code"] = False

#############################
# Admin: Broadcasting & Private
#############################

async def broadcast_to_all(message: str, context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    for u in users:
        try:
            await context.bot.send_message(chat_id=int(u["chat_id"]), text=message)
        except Exception as e:
            logger.error(f"Failed to broadcast to {u['chat_id']}: {e}")

async def private_message_user(chat_id: str, message: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_message(chat_id=int(chat_id), text=message)
    except Exception as e:
        logger.error(f"Failed to private-message {chat_id}: {e}")

#############################
# Admin: Checking Who Forgot
#############################

def check_forgot_entries():
    missing = []
    all_users = get_all_users()

    old_morning = last_reminder_counts.get("morning", {})
    for u in all_users:
        if u["chat_id"] in old_morning:
            old_val = old_morning[u["chat_id"]]
            if (u["morning_count"] or 0) == (old_val or 0):
                missing.append(f"{u['code']} forgot morning entry")

    old_evening = last_reminder_counts.get("evening", {})
    for u in all_users:
        if u["chat_id"] in old_evening:
            old_val = old_evening[u["chat_id"]]
            if (u["night_count"] or 0) == (old_val or 0):
                missing.append(f"{u['code']} forgot evening entry")

    return "\n".join(missing)

#############################
# Admin: Test Reminders
#############################

async def test_morning_reminder_for_admin(context: ContextTypes.DEFAULT_TYPE):
    """
    Send the exact same style as the real morning reminder, but only to ADMIN.
    """
    code = "TEST"
    keyboard = [
        [InlineKeyboardButton("Complete Morning Entry", callback_data=f"morning_{code}")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "Good morning! (TEST)\n"
            "You are TEST, right?\n"
            "Click below to fill your morning diary (TEST)."
        ),
        reply_markup=markup
    )

async def test_evening_reminder_for_admin(context: ContextTypes.DEFAULT_TYPE):
    code = "TEST"
    keyboard = [
        [InlineKeyboardButton("Complete Evening Entry", callback_data=f"evening_{code}")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "Good evening! (TEST)\n"
            "You are TEST, correct?\n"
            "Tap below to fill your evening diary (TEST)."
        ),
        reply_markup=markup
    )

#############################
# Admin: Next Reminders
#############################

def get_next_reminders_info():
    morning_job = scheduler.get_job("morning_reminder")
    evening_job = scheduler.get_job("evening_reminder")
    if not morning_job or not evening_job:
        return "No scheduled jobs found for morning or evening reminder."

    now = datetime.now(tz=SINGAPORE_TZ)
    next_morning = morning_job.next_run_time
    next_evening = evening_job.next_run_time

    delta_m = (next_morning - now).total_seconds()
    delta_e = (next_evening - now).total_seconds()

    if delta_m < 0:
        text_m = "Morning reminder is due very soon or just triggered!"
    else:
        hrs_m, rem_m = divmod(delta_m, 3600)
        mins_m, _ = divmod(rem_m, 60)
        text_m = (f"Next Morning Reminder in {int(hrs_m)} hour(s) and {int(mins_m)} minute(s). "
                  f"({next_morning.astimezone(SINGAPORE_TZ).strftime('%Y-%m-%d %H:%M %Z')})")

    if delta_e < 0:
        text_e = "Evening reminder is due very soon or just triggered!"
    else:
        hrs_e, rem_e = divmod(delta_e, 3600)
        mins_e, _ = divmod(rem_e, 60)
        text_e = (f"Next Evening Reminder in {int(hrs_e)} hour(s) and {int(mins_e)} minute(s). "
                  f"({next_evening.astimezone(SINGAPORE_TZ).strftime('%Y-%m-%d %H:%M %Z')})")

    return text_m + "\n" + text_e

#############################
# Putting It All Together
#############################

def main():
    init_db()
    # If you have old data with NULL counts, run fix_db() once:
    # fix_db()

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

    # Start/restart diary inline
    application.add_handler(CallbackQueryHandler(handle_start_buttons, pattern="^(cont_diary|restart_diary)$"))

    # Schedule reminders
    schedule_jobs(application)

    # Real morning/evening inline flows
    application.add_handler(CallbackQueryHandler(reminder_button_handler, pattern="^(morning_|evening_)"))
    application.add_handler(CallbackQueryHandler(code_mismatch_handler, pattern="^(update_code|restart_diary)$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_code_message))
    application.add_handler(CallbackQueryHandler(done_button_handler, pattern="^done_entry$"))

    # Admin
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(admin_menu_handler, pattern="^(adm_check_progress|adm_find_code|adm_reset_change|adm_forgot|adm_broadcast|adm_private|adm_testall|adm_next_reminders)$"))

    # For typed text after admin has chosen broadcast, private message, find code, etc.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_handler))

    # Handling reset/change code callbacks
    application.add_handler(CallbackQueryHandler(admin_reset_change_callback, pattern="^(adm_reset_|adm_change_)"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_change_code_text))

    # If you want inline code listing for find/reset, define a pattern like "adm_find_|adm_reset_change_|adm_private_"
    application.add_handler(CallbackQueryHandler(admin_code_inline_handler, pattern="^(adm_find_|adm_reset_change_|adm_private_)"))

    application.run_polling()

if __name__ == "__main__":
    main()
