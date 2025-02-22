import logging
import json
import os
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Logging Configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot Token & Moderator ID
BOT_TOKEN = "8108051087:AAFt6oxps6oWQU92Ez30lE2yhS4BesuwEFY"
MODERATOR_ID = 1068291865  # For error notifications

# File for storing user data
USERS_FILE = "user_codes.json"

# Define states for conversation handler
REG_COLOR, REG_ANIMAL, REG_SPORT, REG_AGE = range(4)

# APScheduler instance (initialized once)
scheduler = AsyncIOScheduler()

# ------------------ User Data Handling ------------------ #
def load_users():
    """Load user data from JSON."""
    if not os.path.exists(USERS_FILE):
        return {}

    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load user data: {e}")
        return {}

def save_users(users):
    """Save user data to JSON."""
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=4, ensure_ascii=False)
    except OSError as e:
        logger.error(f"Failed to save user data: {e}")

# ------------------ Registration Flow ------------------ #
async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command. Check if user is registered; if not, begin registration."""
    chat_id = str(update.effective_chat.id)
    users = load_users()

    if chat_id in users:
        # User is already registered
        code = users[chat_id]["code"]
        await update.message.reply_text(f"You are already registered. Your anonymous code is: {code}")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Welcome! Let's get you registered.\nWhat's your favorite color?")
        return REG_COLOR

async def reg_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Please enter a valid color.")
        return REG_COLOR

    context.user_data["color"] = text
    await update.message.reply_text("Great! What's your favorite animal?")
    return REG_ANIMAL

async def reg_animal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Please enter a valid animal.")
        return REG_ANIMAL

    context.user_data["animal"] = text
    await update.message.reply_text("Awesome! What's your favorite sport?")
    return REG_SPORT

async def reg_sport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Please enter a valid sport.")
        return REG_SPORT

    context.user_data["sport"] = text
    await update.message.reply_text("Cool! Lastly, what's your age?")
    return REG_AGE

async def reg_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    age_str = update.message.text.strip()

    if not age_str.isdigit():
        await update.message.reply_text("Please enter a valid number for your age:")
        return REG_AGE

    context.user_data["age"] = age_str

    # Generate codename safely
    color = context.user_data["color"].strip()
    animal = context.user_data["animal"].strip()
    sport = context.user_data["sport"].strip()

    code = f"{color[0].upper()}{animal[0].upper()}{sport[0].upper()}{age_str}"

    users = load_users()
    users[chat_id] = {
        "color": color,
        "animal": animal,
        "sport": sport,
        "age": age_str,
        "code": code,
    }
    save_users(users)

    await update.message.reply_text(f"Registration complete! Your anonymous code is: {code}")
    return ConversationHandler.END

async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel registration."""
    await update.message.reply_text("Registration canceled.")
    return ConversationHandler.END

# ------------------ Reminder System ------------------ #
async def send_morning_reminder(context):
    """Send a morning reminder to all registered users."""
    try:
        users = load_users()
        for chat_id in users.keys():
            await context.bot.send_message(chat_id=int(chat_id), text="Good morning! Please complete your morning diary.")
    except Exception as e:
        logger.error(f"Error in morning reminder: {e}")
        await context.bot.send_message(chat_id=MODERATOR_ID, text=f"Error in morning reminder: {e}")

async def send_evening_reminder(context):
    """Send an evening reminder to all registered users."""
    try:
        users = load_users()
        for chat_id in users.keys():
            await context.bot.send_message(chat_id=int(chat_id), text="Good evening! Please complete your evening diary.")
    except Exception as e:
        logger.error(f"Error in evening reminder: {e}")
        await context.bot.send_message(chat_id=MODERATOR_ID, text=f"Error in evening reminder: {e}")

def schedule_jobs(application):
    """Schedule reminders for morning and evening."""
    if not scheduler.running:
        scheduler.start()

    scheduler.add_job(
        send_morning_reminder,
        trigger=CronTrigger(hour=17, minute=00),
        args=[application],
        id="morning_reminder",
        replace_existing=True
    )
    scheduler.add_job(
        send_evening_reminder,
        trigger=CronTrigger(hour=5, minute=0),
        args=[application],
        id="evening_reminder",
        replace_existing=True
    )

# ------------------ Main ------------------ #
def main():
    """Main function to initialize bot and scheduler."""
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

    # Schedule jobs and start bot
    schedule_jobs(application)
    application.run_polling()

if __name__ == "__main__":
    main()
