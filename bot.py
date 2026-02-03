import sqlite3
from dotenv import load_dotenv
from datetime import date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, Application, CallbackQueryHandler, ContextTypes
import os

load_dotenv() 

TOKEN = os.getenv('API_KEY')
if not TOKEN:
    raise RuntimeError("API_KEY environment variable not set")

def get_db():
    conn = sqlite3.connect("cards.db")
    return conn, conn.cursor()

conn, cur = get_db()

def get_intervals_by_review_count(reviews: int):
    schedule = [
        [("5 hours", 5 / 24)],       
        [("1 day", 1)],              
        [("2 days", 2)],            
        [("3 days", 3)],            
        [("9 days", 9)],            
        [("27 days", 27)],          
        [("54 days", 54)],          
        [("81 days", 81)],          
        [("162 days", 162)],          
    ]

    index = min(reviews, len(schedule) - 1)
    return schedule[index]        

cur.execute("""
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    deck TEXT,
    front TEXT,
    back TEXT,
    interval INTEGER,
    next_review DATE,
    reviews INTEGER DEFAULT 0
)
""")
conn.commit()

cur.execute("""
CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    name TEXT,
    UNIQUE(user_id, name)
)
""")
conn.commit()

cur.execute("""
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    current_deck TEXT DEFAULT 'default'
)
""")
conn.commit()

def get_current_deck(user_id):
    cur.execute(
        "SELECT current_deck FROM user_settings WHERE user_id=?",
        (user_id,)
    )
    row = cur.fetchone()
    return row[0] if row else "default"
            
def truncate(text, width):
    return (text[:width-3] + '...') if len(text) > width else text

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*All commands*\n\n"
        "New deck:\n"
        "`/newdeck Name`\n\n"
        "Choose deck:\n"
        "`/deck Name`\n\n"
        "List decks:\n"
        "`/decks`\n\n"
        "Add card:\n"
        "`/add Question | Answer`\n\n"
        "Review cards:\n"
        "`/review`\n\n"
        "List cards:\n"
        "`/cards`\n\n"
        "Delete card:\n"
        "`/delete ID`\n\n"
        "Reminder:\n"
        "`/reminder Day(s)`\n",
        parse_mode="Markdown"
    )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.replace("/add", "", 1).strip()
        front, back = text.split("|", 1)

        deck = get_current_deck(update.effective_user.id)

        cur.execute("""
            INSERT INTO cards (user_id, deck, front, back, interval, next_review,  reviews)
            VALUES ( ?, ?, ?, ?, ?, ?)
            """,
            (
                update.effective_user.id,
                deck,
                front.strip(),
                back.strip(),
                1,
                date.today().isoformat()
            )
        )
        conn.commit()

        await update.message.reply_text(
            f" Card added to deck `{deck}`",
            parse_mode="Markdown"
        )

    except ValueError:
        await update.message.reply_text(
            "Usage:\n`/add Question | Answer`",
            parse_mode="Markdown"
        )


async def review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deck = get_current_deck(update.effective_user.id)
    today = date.today().isoformat()
    cur.execute(
    "SELECT id, front FROM cards WHERE user_id=? AND deck=? AND next_review<=?",
    (update.effective_user.id, deck, today)
    )
    card = cur.fetchone()

    if not card:
        await update.message.reply_text(
            f" No cards due in `{deck}`",
            parse_mode="Markdown"
        )
        return

    card_id, front = card
    context.user_data["card_id"] = card_id

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Show answer", callback_data="show")]
    ])

    await update.message.reply_text(
        f" *Question*\n\n{front}",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    card_id = context.user_data.get("card_id")

    if not card_id:
        await query.edit_message_text("No active card.")
        return
    
    if query.data == "show":
        cur.execute(
            "SELECT front, back, reviews FROM cards WHERE id=?",
            (card_id,)
        )
        front, back, reviews = cur.fetchone()

        intervals = get_intervals_by_review_count(reviews)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(label, callback_data=f"d_{days}")
                for label, days in intervals
            ]
        ])

        await query.edit_message_text(
            f"*Question*\n{front}\n\n"
            f"*Answer*\n{back}\n\n"
            "*When should I show it again?*",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

    elif query.data.startswith("d_"):
        days = float(query.data.split("_")[1])
        next_review = date.today() + timedelta(days=days)

        cur.execute(
            "UPDATE cards SET interval=?, next_review=?, reviews = reviews + 1  WHERE id=?",
            (days, next_review, card_id)
        )
        conn.commit()

        if days < 1:
            hours = int(days * 24)
            time_text = f"{hours} hour(s)"
        else:
            time_text = f"{int(days)} day(s)"

        await query.edit_message_text(
            f" Scheduled in *{time_text}*\n {next_review}",
            parse_mode="Markdown"
        )

    elif query.data == "delete_confirm":
        card_id = context.user_data.get("delete_card_id")

        if not card_id:
            await query.edit_message_text("Nothing to delete.")
            return

        cur.execute("DELETE FROM cards WHERE id=?", (card_id,))
        conn.commit()

        await query.edit_message_text("Card deleted.")

    elif query.data == "delete_cancel":
        await query.edit_message_text("Delete canceled.")


async def cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deck = get_current_deck(update.effective_user.id)
    cur.execute(
        "SELECT id, front, back, next_review FROM cards WHERE user_id=? AND deck=? ORDER BY id",
        (update.effective_user.id, deck)
    )
    rows = cur.fetchall()

    if not rows:
        await update.message.reply_text(" No cards found.")
        return

    ID_WIDTH = 3
    FRONT_WIDTH = 10
    BACK_WIDTH = 15

    message = "*Your cards*\n\n"
    message += "```\n"
    
    message += f"{'ID'.ljust(ID_WIDTH)} | {'Front'.ljust(FRONT_WIDTH)}  {'Back'.ljust(BACK_WIDTH)} | {'Review'}\n"
    message += "-"*60 + "\n"

    for card_id, front, back, next_review in rows:
        front_display = truncate(front, FRONT_WIDTH).ljust(FRONT_WIDTH)
        back_display = truncate(back, BACK_WIDTH).ljust(BACK_WIDTH)
        review_display = str(next_review)

        message += f"{str(card_id).ljust(ID_WIDTH)} | {front_display}  {back_display} | {review_display}\n"
        
    message += "```"
    
    await update.message.reply_text(message,parse_mode="Markdown"
    )


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        card_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text(
            " Usage: `/del <card_id>`",
            parse_mode="Markdown"
        )
        return

    cur.execute(
        "SELECT id FROM cards WHERE id=? AND user_id=?",
        (card_id, update.effective_user.id)
    )

    if not cur.fetchone():
        await update.message.reply_text("Card not found.")
        return

    cur.execute(
        "DELETE FROM cards WHERE id=? AND user_id=?",
        (card_id, update.effective_user.id)
    )
    conn.commit()

    await update.message.reply_text(f" Card deleted (ID {card_id})",
    )


async def review_reminder(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.chat_id
    today = date.today().isoformat()

    cur.execute(
    "SELECT deck, COUNT(*) FROM cards WHERE user_id = ? AND next_review <= ? GROUP BY deck",
        (user_id, today)
    )
    rows = cur.fetchall()

    if not rows:
        return
    
    message = "*Review reminder*\n\n"

    for deck, count in rows:
        message += f" `{deck}` — *{count}* cards\n"

    message += "Use /review to start."
        
    await context.bot.send_message(
        chat_id=user_id,
        text=message,
        parse_mode="Markdown"
    )
       

async def reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            " Usage:\n`/reminder <days>`\n\nExample:\n`/reminder 3`",
            parse_mode="Markdown"
        )
        return

    try:
        days = int(context.args[0])
        if days < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text(" Please enter a valid number of days.")
        return

    job_name = str(update.effective_chat.id)

    current_jobs = context.application.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()

    context.application.job_queue.run_repeating(
        review_reminder,
        interval=timedelta(days=days),
        first=timedelta(seconds=5),
        chat_id=update.effective_chat.id,
        name=job_name,
    )

    await update.message.reply_text(
        f" Reminder updated!\n\nI will remind you every *{days}* day(s).",
        parse_mode="Markdown"
    )

async def newdeck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /newdeck <name>")
        return

    deck_name = context.args[0].lower()
    user_id = update.effective_user.id

    cur.execute(
        "INSERT INTO decks (user_id, name) VALUES (?, ?)",
        (user_id, deck_name)
    )
    conn.commit()

    await update.message.reply_text(f" Deck `{deck_name}` created!",
        parse_mode="Markdown")

async def deck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /deck <name>")
        return

    deck = context.args[0]
    user_id = update.effective_user.id

    cur.execute(
        "SELECT 1 FROM decks WHERE user_id=? AND name=?",
        (user_id, deck)
    )
    if not cur.fetchone():
        await update.message.reply_text(" Deck not found.")
        return

    cur.execute(
        """
        INSERT INTO user_settings (user_id, current_deck)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET current_deck=excluded.current_deck
        """,
        (user_id, deck)
    )
    conn.commit()

    await update.message.reply_text(f" Using deck `{deck}`", parse_mode="Markdown")

async def decks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute(
        "SELECT name FROM decks WHERE user_id=?",
        (update.effective_user.id,)
    )
    rows = cur.fetchall()

    if not rows:
        await update.message.reply_text(" No decks yet.")
        return

    text = "*Your decks:*\n\n"
    for name in rows:
        text += f" `{name}`\n"

    await update.message.reply_text(text, parse_mode="Markdown")

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("review", review))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(CommandHandler("cards", cards))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("reminder", reminder))
    app.add_handler(CommandHandler("newdeck", newdeck))
    app.add_handler(CommandHandler("deck", deck))
    app.add_handler(CommandHandler("decks", decks))

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
