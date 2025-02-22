import logging
import json
import os
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from telegram.ext import CallbackContext
from telegram.ext import (
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8108051087:AAFt6oxps6oWQU92Ez30lE2yhS4BesuwEFY"
MODERATOR_ID = 1068291865  # for error notifications

USERS_FILE = "user_codes.json"  # JSON for storing user data

# Registration conversation states
REG_COLOR, REG_ANIMAL, REG_SPORT, REG_AGE, REG_DONE = range(5)

# APScheduler instance
scheduler = AsyncIOScheduler()

def load_users():
    """Load user data (codename, color, animal, etc.) from JSON."""
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_users(users):
    """Save user data to JSON."""
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)

# ------------------ Registration Flow ------------------ #
async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command. Checks if user is registered, if not begins registration."""
    chat_id = str(update.effective_chat.id)
    users = load_users()

    if chat_id in users:
        # Already registered
        code = users[chat_id]["code"]
        await update.message.reply_text(
            f"You are already registered. Your anonymous code is: {code}"
        )
        return ConversationHandler.END
    else:
        # Begin registration
        await update.message.reply_text(
            "Welcome! Let's get you registered.\nWhat's your favorite color?"
        )
        return REG_COLOR

async def reg_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["color"] = update.message.text.strip()
    await update.message.reply_text("Great! What's your favorite animal?")
    return REG_ANIMAL

async def reg_animal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["animal"] = update.message.text.strip()
    await update.message.reply_text("Awesome! What's your favorite sport?")
    return REG_SPORT

async def reg_sport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["sport"] = update.message.text.strip()
    await update.message.reply_text("Cool! Lastly, what's your age?")
    return REG_AGE

async def reg_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    age_str = update.message.text.strip()

    # Basic validation for age
    if not age_str.isdigit():
        await update.message.reply_text("Please enter a valid number for your age:")
        return REG_AGE

    context.user_data["age"] = age_str

    # Generate codename
    color_initial = context.user_data["color"][0].upper()
    animal_initial = context.user_data["animal"][0].upper()
    sport_initial = context.user_data["sport"][0].upper()
    age = context.user_data["age"]

    code = f"{color_initial}{animal_initial}{sport_initial}{age}"
    context.user_data["code"] = code

    # Save to JSON
    users = load_users()
    users[chat_id] = {
        "color": context.user_data["color"],
        "animal": context.user_data["animal"],
        "sport": context.user_data["sport"],
        "age": age,
        "code": code,
    }
    save_users(users)

    await update.message.reply_text(
        f"Registration complete! Your anonymous code is: {code}\nUse this code for diary entries."
    )
    return ConversationHandler.END

async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels registration if user sends /cancel."""
    await update.message.reply_text("Registration canceled.")
    return ConversationHandler.END

# ------------------ Reminders ------------------ #
async def send_morning_reminder(context: CallbackContext):
    try:
        # For simplicity, broadcast to all registered users
        users = load_users()
        for chat_id in users.keys():
            await context.bot.send_message(
                chat_id=int(chat_id),
                text="Good morning! Please complete your morning diary."
            )
    except Exception as e:
        logger.error(f"Error in morning reminder: {e}")
        await context.bot.send_message(chat_id=MODERATOR_ID, text=f"Error in morning reminder: {e}")

async def send_evening_reminder(context: CallbackContext):
    try:
        users = load_users()
        for chat_id in users.keys():
            await context.bot.send_message(
                chat_id=int(chat_id),
                text="Good evening! Please complete your evening diary."
            )
    except Exception as e:
        logger.error(f"Error in evening reminder: {e}")
        await context.bot.send_message(chat_id=MODERATOR_ID, text=f"Error in evening reminder: {e}")

def schedule_jobs(application):
    scheduler.add_job(
        send_morning_reminder,
        trigger=CronTrigger(hour=8, minute=0),
        args=[application],
        id="morning_reminder"
    )
    scheduler.add_job(
        send_evening_reminder,
        trigger=CronTrigger(hour=20, minute=0),
        args=[application],
        id="evening_reminder"
    )

# ------------------ Main ------------------ #
def main():
    from telegram.ext import Application
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Registration conversation
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_registration)],
        states={
            REG_COLOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_color)],
            REG_ANIMAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_animal)],
            REG_SPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_sport)],
            REG_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_age)],
        },
        fallbacks=[CommandHandler("cancel", reg_cancel)],
    )
    application.add_handler(conv_handler)

    # Start APScheduler
    scheduler.start()
    schedule_jobs(application)

    application.run_polling()

if __name__ == "__main__":
    main()
