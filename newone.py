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
                [InlineKeyboardButton("Complete Morning Entry", callback_data=f"morning_{code}")],
                [InlineKeyboardButton("Contact Admin", callback_data=f"contactadmin_{code}")]
            ]
            markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=(
                    f"Good morning!\n"
                    f"You are {code}, right?\n"
                    "Click below to fill your morning diary or contact admin."
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
                [InlineKeyboardButton("Complete Evening Entry", callback_data=f"evening_{code}")],
                [InlineKeyboardButton("Contact Admin", callback_data=f"contactadmin_{code}")]
            ]
            markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=(
                    f"Good evening!\n"
                    f"You are {code}, correct?\n"
                    "Tap below to fill your evening diary or contact admin."
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
# Participant -> Admin typed message flow
#############################

async def reminder_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("contactadmin_"):
        # typed message flow
        parts = data.split("_", 1)
        if len(parts) != 2:
            await query.edit_message_text("Contact Admin: invalid data.")
            return
        user_code = parts[1]
        context.user_data["p2a_user_code"] = user_code
        context.user_data["p2a_typed_message"] = True
        await query.edit_message_text("Please type the message you want to send to the admin.")
        return

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
        # normal morning/evening
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
# Participant->Admin confirm
#############################
async def participant_to_admin_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    if choice == "p2a_confirm_yes":
        msg_text = context.user_data.get("p2a_msg_text", "")
        user_code = context.user_data.get("p2a_user_code", "")
        if not user_code:
            await query.edit_message_text("No codename found. Aborting.")
            return
        # send to admin
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"[{user_code}] says:\n{msg_text}\n\nUse /admin -> 'Private Message a Participant' to reply."
        )
        await query.edit_message_text("Your message has been sent to the admin.")
        context.user_data["p2a_msg_text"] = ""
        context.user_data["p2a_user_code"] = ""
    else:
        # p2a_confirm_no
        await query.edit_message_text("Message canceled.")
        context.user_data["p2a_msg_text"] = ""
        context.user_data["p2a_user_code"] = ""

#############################
# Admin
#############################

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await show_inline_all_codes(query, prefix="adm_find_")
    elif choice == "adm_reset_change":
        await show_inline_all_codes(query, prefix="adm_reset_change_")
    elif choice == "adm_forgot":
        text = check_forgot_entries()
        await query.edit_message_text(text or "No missing entries found.")
    elif choice == "adm_broadcast":
        await query.edit_message_text("Please type the message to broadcast to all participants.")
        context.user_data["adm_broadcast"] = True
    elif choice == "adm_private":
        await show_inline_all_codes(query, prefix="adm_private_")
    elif choice == "adm_testall":
        # define them so we don't crash
        await query.edit_message_text("Sending test reminders to admin only...")
        await test_morning_reminder_for_admin(context)
        await test_evening_reminder_for_admin(context)
        await query.message.reply_text("Test complete!")
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
        m_count = u["morning_count"]
        n_count = u["night_count"]
        remain_m = max(0, TARGET_COUNT - m_count)
        remain_n = max(0, TARGET_COUNT - n_count)
        lines.append(
            f"Code: {u['code']}, M:{m_count}/{TARGET_COUNT} "
            f"(left {remain_m}), E:{n_count}/{TARGET_COUNT} "
            f"(left {remain_n})"
        )
    await query.edit_message_text("\n".join(lines) or "No data.")

async def show_inline_all_codes(query, prefix):
    users = get_all_users()
    if not users:
        await query.edit_message_text("No users found.")
        return

    buttons = []
    row = []
    for u in users:
        code = u["code"]
        callback_data = f"{prefix}{code}"
        row.append(InlineKeyboardButton(code, callback_data=callback_data))
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
    query = update.callback_query
    await query.answer()
    data = query.data

    parts = data.split("_", 2)
    if len(parts) < 3:
        # fallback
        keyboard = [[InlineKeyboardButton("Contact Admin", url="t.me/...")]]
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "I received your button click but I don’t know how to handle it.\nData format invalid.",
            reply_markup=markup
        )
        return

    prefix = parts[0]  # "adm"
    subprefix = parts[1]  # "find", "reset_change", "private", etc.
    codename = parts[2]

    if prefix == "adm":
        if subprefix == "find":
            user = load_user_by_code(codename)
            if user:
                m_count = user["morning_count"]
                n_count = user["night_count"]
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

        elif subprefix == "reset_change":
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

        elif subprefix == "private":
            user = load_user_by_code(codename)
            if not user:
                await query.edit_message_text(f"No user found with code {codename}.")
                return
            context.user_data["adm_private_chatid"] = user["chat_id"]
            context.user_data["adm_private_msg"] = True
            await query.edit_message_text(
                f"Please type the message you want to send to {codename}."
            )

        else:
            keyboard = [[InlineKeyboardButton("Contact Admin", url="t.me/...")]]
            markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                f"I received your button click but I don’t know how to handle subprefix '{subprefix}'.",
                reply_markup=markup
            )
    else:
        keyboard = [[InlineKeyboardButton("Contact Admin", url="t.me/...")]]
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"I received your button click but prefix '{prefix}' is not recognized.",
            reply_markup=markup
        )

#############################
# Admin: "adm_reset_" or "adm_change_"
#############################
async def admin_reset_change_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("_", 2)
    if len(parts) != 3:
        keyboard = [[InlineKeyboardButton("Contact Admin", url="t.me/...")]]
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "I received your button click but I don’t know how to handle it.\nData format invalid.",
            reply_markup=markup
        )
        return

    action = parts[1]
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
    else:
        keyboard = [[InlineKeyboardButton("Contact Admin", url="t.me/...")]]
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"I received your button click but I don’t know how to handle action '{action}'.",
            reply_markup=markup
        )

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
### ADDED ###
async def test_morning_reminder_for_admin(context: ContextTypes.DEFAULT_TYPE):
    """Stub function so we don't crash when adm_testall is chosen."""
    code = "TEST"
    keyboard = [[InlineKeyboardButton("Complete Morning Entry", callback_data=f"morning_{code}")]]
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
    """Stub function so we don't crash when adm_testall is chosen."""
    code = "TEST"
    keyboard = [[InlineKeyboardButton("Complete Evening Entry", callback_data=f"evening_{code}")]]
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
# Single Text Handler
#############################

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # If user typed new code from "change_code_start"
    if context.user_data.get("change_code_from_start"):
        context.user_data["change_code_from_start"] = False
        user = load_user(chat_id)
        if not user:
            await update.message.reply_text("You’re not registered. Please /start.")
            return
        update_user_code(chat_id, text)
        await update.message.reply_text(f"Your code is updated to {text}.")
        return

    # Admin flows
    if chat_id == ADMIN_ID:
        # broadcast
        if context.user_data.get("adm_broadcast"):
            context.user_data["adm_broadcast"] = False
            context.user_data["adm_broadcast_text"] = text
            keyboard = [
                [
                    InlineKeyboardButton("Yes", callback_data="adm_broadcast_confirm_yes"),
                    InlineKeyboardButton("No", callback_data="adm_broadcast_confirm_no")
                ]
            ]
            markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"Are you sure you want to broadcast this message?\n\n{text}",
                reply_markup=markup
            )
            return

        # private
        if context.user_data.get("adm_private_msg"):
            context.user_data["adm_private_msg"] = False
            context.user_data["adm_private_text"] = text
            keyboard = [
                [
                    InlineKeyboardButton("Yes", callback_data="adm_private_confirm_yes"),
                    InlineKeyboardButton("No", callback_data="adm_private_confirm_no")
                ]
            ]
            markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"Are you sure you want to send this message?\n\n{text}",
                reply_markup=markup
            )
            return

        # changing code
        if context.user_data.get("adm_changing_code"):
            new_code = text
            user_chatid = context.user_data["adm_change_user"]
            user = load_user(user_chatid)
            if not user:
                await update.message.reply_text("User not found or no longer exists.")
                context.user_data["adm_changing_code"] = False
                return
            update_user_code(user_chatid, new_code)
            await update.message.reply_text(f"User code updated to {new_code}.")
            context.user_data["adm_changing_code"] = False
            return

    # Participant typed new code after mismatch
    if context.user_data.get("awaiting_new_code"):
        new_code = text
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
        return

    # Participant typed message for admin (two-way flow)
    if context.user_data.get("p2a_typed_message"):
        context.user_data["p2a_typed_message"] = False
        context.user_data["p2a_msg_text"] = text
        keyboard = [
            [
                InlineKeyboardButton("Yes", callback_data="p2a_confirm_yes"),
                InlineKeyboardButton("No", callback_data="p2a_confirm_no")
            ]
        ]
        markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"Are you sure you want to send this message to the admin?\n\n{text}",
            reply_markup=markup
        )
        return

    # fallback
    await update.message.reply_text("No recognized state for this text. Please use /admin or /start if needed.")

#############################
# Admin Confirmation Callbacks
#############################

async def admin_broadcast_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    if choice == "adm_broadcast_confirm_yes":
        text = context.user_data.get("adm_broadcast_text", "")
        if text:
            await broadcast_to_all(text, context)
            await query.edit_message_text("Broadcast sent to all participants.")
        else:
            await query.edit_message_text("No broadcast text found. Nothing sent.")
        context.user_data["adm_broadcast_text"] = ""
    else:
        await query.edit_message_text("Broadcast canceled.")
        context.user_data["adm_broadcast_text"] = ""

async def admin_private_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    if choice == "adm_private_confirm_yes":
        p_chatid = context.user_data.get("adm_private_chatid", None)
        msg = context.user_data.get("adm_private_text", "")
        if p_chatid and msg:
            # send to participant
            await private_message_user(p_chatid, f"[ADMIN]: {msg}", context)
            await query.edit_message_text("Private message sent to participant.")
        else:
            await query.edit_message_text("No message or participant found. Nothing sent.")
        context.user_data["adm_private_chatid"] = None
        context.user_data["adm_private_text"] = ""
    else:
        # "adm_private_confirm_no"
        await query.edit_message_text("Private message canceled.")
        context.user_data["adm_private_chatid"] = None
        context.user_data["adm_private_text"] = ""

#############################
# Test Morning/Evening Reminders
#############################
### ADDED ###
async def test_morning_reminder_for_admin(context: ContextTypes.DEFAULT_TYPE):
    code = "TEST"
    keyboard = [[InlineKeyboardButton("Complete Morning Entry", callback_data=f"morning_{code}")]]
    markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text="Good morning! (TEST)\nClick below to fill your morning diary (TEST).",
        reply_markup=markup
    )

async def test_evening_reminder_for_admin(context: ContextTypes.DEFAULT_TYPE):
    code = "TEST"
    keyboard = [[InlineKeyboardButton("Complete Evening Entry", callback_data=f"evening_{code}")]]
    markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text="Good evening! (TEST)\nClick below to fill your evening diary (TEST).",
        reply_markup=markup
    )

#############################
# Next Reminders Info
#############################

def get_next_reminders_info():
    morning_job = scheduler.get_job("morning_reminder")
    evening_job = scheduler.get_job("evening_reminder")
    if not morning_job or not evening_job:
        return "No scheduled jobs found."

    now = datetime.now(tz=SINGAPORE_TZ)
    nm = morning_job.next_run_time
    ne = evening_job.next_run_time

    dm = (nm - now).total_seconds()
    de = (ne - now).total_seconds()

    if dm < 0:
        text_m = "Morning reminder is due soon or triggered!"
    else:
        hrs_m, rem_m = divmod(dm, 3600)
        mins_m, _ = divmod(rem_m, 60)
        text_m = (f"Next Morning Reminder in {int(hrs_m)}h {int(mins_m)}m. "
                  f"({nm.astimezone(SINGAPORE_TZ).strftime('%Y-%m-%d %H:%M %Z')})")

    if de < 0:
        text_e = "Evening reminder is due soon or triggered!"
    else:
        hrs_e, rem_e = divmod(de, 3600)
        mins_e, _ = divmod(rem_e, 60)
        text_e = (f"Next Evening Reminder in {int(hrs_e)}h {int(mins_e)}m. "
                  f"({ne.astimezone(SINGAPORE_TZ).strftime('%Y-%m-%d %H:%M %Z')})")

    return text_m + "\n" + text_e

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
