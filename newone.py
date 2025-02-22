import asyncio
import logging
from datetime import datetime, timedelta

from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8108051087:AAFt6oxps6oWQU92Ez30lE2yhS4BesuwEFY"

# Create a single, global APScheduler instance
scheduler = AsyncIOScheduler()

async def start_command(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! I'm running in a single event loop with APScheduler.")

async def send_morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    try:
        # Example: broadcast to a known chat_id
        chat_id = 1068291865  # Replace with real chat ID or iterate over your user list
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
    # You can also use a CronTrigger or IntervalTrigger
    scheduler.add_job(
        send_morning_reminder,
        trigger=CronTrigger(hour=8, minute=0),  # 8:00 AM daily
        args=[application],
        id="morning_reminder"
    )
    scheduler.add_job(
        send_evening_reminder,
        trigger=CronTrigger(hour=20, minute=0), # 8:00 PM daily
        args=[application],
        id="evening_reminder"
    )

async def main():
    """
    The main async function that sets up the bot, scheduler, and starts polling.
    """
    # 1. Build the bot application
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # 2. Add handlers
    application.add_handler(CommandHandler("start", start_command))

    # 3. Start the scheduler (tie it to the current event loop)
    #    Make sure to do this before scheduling jobs
    scheduler.start()

    # 4. Schedule your jobs, passing 'application' so the job callbacks have access to the bot context
    schedule_jobs(application)

    # 5. Run the bot until you press Ctrl-C or the process receives SIGINT, SIGTERM or SIGABRT
    await application.run_polling()

if __name__ == "__main__":
    # 6. We only call asyncio.run(main()) once. This ensures a single event loop.
    asyncio.run(main())
