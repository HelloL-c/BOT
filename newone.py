import logging
from datetime import datetime, timedelta

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8108051087:AAFt6oxps6oWQU92Ez30lE2yhS4BesuwEFY"

# Create a single, global APScheduler instance
scheduler = AsyncIOScheduler()

async def start_command(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello! I'm running with APScheduler and run_polling() in synchronous style."
    )

async def send_morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = 1068291865  # Replace with your actual chat_id or loop over user list
        await context.bot.send_message(chat_id=chat_id, text="Good morning reminder!")
    except Exception as e:
        logger.error(f"Error in morning reminder: {e}")

async def send_evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = 1068291865
        await context.bot.send_message(chat_id=chat_id, text="Good evening reminder!")
    except Exception as e:
        logger.error(f"Error in evening reminder: {e}")

def schedule_jobs(application):
    """
    Schedule your APScheduler jobs. This function should be called
    after the bot is created but before run_polling().
    """
    scheduler.add_job(
        send_morning_reminder,
        trigger=CronTrigger(hour=8, minute=0),  # 8:00 AM daily
        args=[application],
        id="morning_reminder"
    )
    scheduler.add_job(
        send_evening_reminder,
        trigger=CronTrigger(hour=20, minute=0),  # 8:00 PM daily
        args=[application],
        id="evening_reminder"
    )

def main():
    """
    Synchronous main function that sets up the bot, APScheduler, and runs polling.
    No asyncio.run(...) is called here, so we avoid event loop conflicts.
    """
    # 1. Build the bot application
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # 2. Add command handlers
    application.add_handler(CommandHandler("start", start_command))

    # 3. Start the APScheduler
    scheduler.start()

    # 4. Schedule your jobs
    schedule_jobs(application)

    # 5. Run the bot in a blocking call (managing the event loop internally)
    application.run_polling()

if __name__ == "__main__":
    main()
