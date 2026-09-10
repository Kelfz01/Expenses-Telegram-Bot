import os
import logging
from io import BytesIO
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

import database
import parser
import queries

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

ALLOWED_USER_IDS = [
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
]

def is_authorized(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS

def get_tx_inline_keyboard(tx_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("✏️ Food", callback_data=f"setcat_{tx_id}_Food & Dining"),
            InlineKeyboardButton("✏️ Transport", callback_data=f"setcat_{tx_id}_Transport"),
            InlineKeyboardButton("✏️ Shopping", callback_data=f"setcat_{tx_id}_Shopping"),
        ],
        [
            InlineKeyboardButton("✏️ Bills/Utilities", callback_data=f"setcat_{tx_id}_Bills & Utilities"),
            InlineKeyboardButton("✏️ General", callback_data=f"setcat_{tx_id}_General"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"deltx_{tx_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("Unauthorized user.")
        return

    text = (
        "👋 *Hello! I am your Expense & Income Bot.*\n\n"
        "📸 *How to track:*\n"
        "• Send or share any bank transfer/payment slip photo.\n"
        "• I will auto-detect amount, date, receiver, and category.\n"
        "• Quick category buttons and delete button will appear under each slip!\n\n"
        "✏️ *How to edit or change details:*\n"
        "• `/edit <id> category <new category>` (e.g. `/edit 5 category Coffee`)\n"
        "• `/edit <id> amount <new amount>` (e.g. `/edit 5 amount 120.50`)\n"
        "• `/edit <id> note <new note>` (e.g. `/edit 5 note Dinner with team`)\n"
        "• Or just natural language: _'change category of transaction 5 to Food'_\n"
        "• `/delete <id>` to delete a record\n\n"
        "📊 *How to query:*\n"
        "• 'Total expense today'\n"
        "• 'How much did I spend this week?'\n"
        "• 'Show food expenses this month'\n"
        "• 'Show recent transactions'\n\n"
        f"Your Telegram User ID: `{user_id}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    args = context.args
    if not args or len(args) < 3:
        await update.message.reply_text(
            "Usage: `/edit <id> <field> <value>`\n"
            "Fields: `category`, `amount`, `note`, `type`\n"
            "Example: `/edit 3 category Food & Dining`",
            parse_mode="Markdown"
        )
        return

    try:
        tx_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid transaction ID. Example: `/edit 3 category Food`", parse_mode="Markdown")
        return

    field = args[1].lower()
    value = " ".join(args[2:])

    tx = database.get_transaction_by_id(user_id, tx_id)
    if not tx:
        await update.message.reply_text(f"Transaction #{tx_id} not found.")
        return

    update_kwargs = {}
    if field in ("cat", "category"):
        update_kwargs["category"] = value
    elif field in ("amt", "amount"):
        try:
            update_kwargs["amount"] = float(value.replace(",", ""))
        except ValueError:
            await update.message.reply_text("Amount must be a valid number.")
            return
    elif field in ("note", "raw_note", "remark", "memo"):
        update_kwargs["raw_note"] = value
    elif field in ("type", "trans_type"):
        if value.lower() not in ("expense", "income"):
            await update.message.reply_text("Type must be 'expense' or 'income'.")
            return
        update_kwargs["trans_type"] = value.lower()
    else:
        await update.message.reply_text("Valid fields to edit are: `category`, `amount`, `note`, `type`", parse_mode="Markdown")
        return

    success = database.update_transaction(user_id, tx_id, **update_kwargs)
    if success:
        updated = database.get_transaction_by_id(user_id, tx_id)
        await update.message.reply_text(
            f"✅ Transaction #{tx_id} updated!\n"
            f"• Type: {updated['trans_type'].upper()}\n"
            f"• Amount: {updated['amount']:,.2f}\n"
            f"• Category: {updated['category']}\n"
            f"• Note: {updated['raw_note'] or 'None'}"
        )
    else:
        await update.message.reply_text("Failed to update transaction.")

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: `/delete <id>` (e.g. `/delete 3`)", parse_mode="Markdown")
        return

    tx_id = int(args[0])
    success = database.delete_transaction(user_id, tx_id)
    if success:
        await update.message.reply_text(f"🗑️ Transaction #{tx_id} has been deleted.")
    else:
        await update.message.reply_text(f"Transaction #{tx_id} not found or could not be deleted.")

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    data = query.data
    if data.startswith("setcat_"):
        parts = data.split("_", 2)
        tx_id = int(parts[1])
        new_cat = parts[2]
        if database.update_transaction(user_id, tx_id, category=new_cat):
            await query.edit_message_text(
                f"{query.message.text}\n\n✏️ *Category updated to:* {new_cat}",
                parse_mode="Markdown"
            )
    elif data.startswith("deltx_"):
        parts = data.split("_", 1)
        tx_id = int(parts[1])
        if database.delete_transaction(user_id, tx_id):
            await query.edit_message_text(f"🗑️ Transaction #{tx_id} has been deleted.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    status_msg = await update.message.reply_text("Analyzing slip with Gemini...")

    try:
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        
        photo_bytes = BytesIO()
        await photo_file.download_to_memory(photo_bytes)
        photo_bytes.seek(0)

        slip = parser.parse_slip_image(photo_bytes.read(), mime_type="image/jpeg")

        if not slip or not slip.is_valid_slip:
            await status_msg.edit_text("Could not recognize this as a valid payment slip. Please check image clarity.")
            return

        tx_id = database.add_transaction(
            user_id=user_id,
            trans_type=slip.trans_type,
            amount=slip.amount,
            category=slip.category,
            raw_note=slip.raw_note,
            sender=slip.sender,
            receiver=slip.receiver,
            bank=slip.bank,
            trans_datetime=slip.trans_datetime
        )

        reply = (
            f"✅ Recorded successfully!\n\n"
            f"🆔 ID: #{tx_id}\n"
            f"💵 Amount: {slip.amount:,.2f} {slip.currency or 'THB'}\n"
            f"🏷️ Category: {slip.category}\n"
            f"📝 Note: {slip.raw_note or 'None'}\n"
            f"👤 To: {slip.receiver or 'Unknown'}\n"
            f"📅 Date: {slip.trans_datetime}\n\n"
            f"_Tap a button below to quick-change category or delete:_"
        )
        await status_msg.edit_text(
            reply,
            reply_markup=get_tx_inline_keyboard(tx_id),
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.exception("Error processing slip")
        await status_msg.edit_text(f"Error processing slip: {str(e)}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    user_text = update.message.text.strip()
    try:
        reply = queries.answer_user_query(user_id, user_text)
        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error answering query")
        await update.message.reply_text(f"Error processing question: {str(e)}")

def main():
    database.init_db()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is not set in .env file.")
        return

    print("Starting Telegram Expense Bot...")
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("edit", edit_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling()

if __name__ == "__main__":
    main()
