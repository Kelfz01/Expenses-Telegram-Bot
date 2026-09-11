import os
import logging
from io import BytesIO
from datetime import datetime
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
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

# Persistent reply keyboard for one-tap queries
def get_main_menu_keyboard():
    keyboard = [
        [KeyboardButton("💰 Current Balance"), KeyboardButton("📅 Expense Today")],
        [KeyboardButton("📊 Expense This Week"), KeyboardButton("🗓️ Expense This Month")],
        [KeyboardButton("🕒 Recent Transactions"), KeyboardButton("❓ Help")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_tx_inline_keyboard(tx_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("✏️ Food", callback_data=f"setcat_{tx_id}_Food & Dining"),
            InlineKeyboardButton("✏️ Transport", callback_data=f"setcat_{tx_id}_Transport"),
            InlineKeyboardButton("✏️ Shopping", callback_data=f"setcat_{tx_id}_Shopping"),
        ],
        [
            InlineKeyboardButton("✏️ Bills", callback_data=f"setcat_{tx_id}_Bills & Utilities"),
            InlineKeyboardButton("💳 Paid via Cash", callback_data=f"setacc_{tx_id}_cash"),
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
        "👋 *Welcome to your Expense & Balance Bot!*\n\n"
        "💡 *What I can do:*\n"
        "• *Track Slips:* Send any bank slip photo, and I'll record amount, date, receiver & category.\n"
        "• *Balance Tracking:* Bank accounts & Cash are tracked separately!\n"
        "• *Quick Menu:* Tap any button below for instant summaries.\n\n"
        "⚙️ *Setup Your Initial Balance:*\n"
        "• `/setbalance bank <amount>` (e.g. `/setbalance bank 50000`)\n"
        "• `/setbalance cash <amount>` (e.g. `/setbalance cash 2500`)\n"
        "• Or record manual cash spending: `/cash <amount> <category> <note>`\n\n"
        "✏️ *Editing Transactions:*\n"
        "• `/edit <id> category <new category>`\n"
        "• `/edit <id> amount <new amount>`\n"
        "• `/delete <id>`\n\n"
        f"Your Telegram User ID: `{user_id}`"
    )
    await update.message.reply_text(
        text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode="Markdown"
    )

async def set_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "• `/setbalance bank 50000` (set bank balance)\n"
            "• `/setbalance cash 3000` (set cash balance)",
            parse_mode="Markdown"
        )
        return

    target = args[0].lower()
    try:
        amount = float(args[1].replace(",", ""))
    except ValueError:
        await update.message.reply_text("Please provide a valid numeric amount.")
        return

    if target in ("bank", "account"):
        database.set_initial_balance(user_id, initial_bank=amount)
        await update.message.reply_text(f"✅ Initial bank balance set to: `{amount:,.2f} THB`\n\n" + queries.format_balance_message(user_id), parse_mode="Markdown")
    elif target in ("cash", "wallet"):
        database.set_initial_balance(user_id, initial_cash=amount)
        await update.message.reply_text(f"✅ Initial cash balance set to: `{amount:,.2f} THB`\n\n" + queries.format_balance_message(user_id), parse_mode="Markdown")
    else:
        await update.message.reply_text("Target must be either `bank` or `cash`.\nExample: `/setbalance bank 25000`", parse_mode="Markdown")

async def cash_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    args = context.args
    if not args or len(args) < 1:
        await update.message.reply_text(
            "Usage: `/cash <amount> [category] [note]`\n"
            "Example: `/cash 60 Food Lunch with cash`",
            parse_mode="Markdown"
        )
        return

    try:
        amount = float(args[0].replace(",", ""))
    except ValueError:
        await update.message.reply_text("Please enter a valid amount.")
        return

    category = args[1] if len(args) > 1 else "General"
    raw_note = " ".join(args[2:]) if len(args) > 2 else "Cash payment"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    tx_id = database.add_transaction(
        user_id=user_id,
        trans_type="expense",
        amount=amount,
        category=category,
        raw_note=raw_note,
        sender="Me",
        receiver="Cash Payment",
        bank="Cash",
        trans_datetime=now_str,
        account_type="cash"
    )

    await update.message.reply_text(
        f"💵 *Cash Expense Recorded!*\n\n"
        f"🆔 ID: #{tx_id}\n"
        f"💸 Amount: `{amount:,.2f} THB`\n"
        f"🏷️ Category: {category}\n"
        f"📝 Note: {raw_note}\n\n"
        f"{queries.format_balance_message(user_id)}",
        reply_markup=get_tx_inline_keyboard(tx_id),
        parse_mode="Markdown"
    )

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    args = context.args
    if not args or len(args) < 3:
        await update.message.reply_text(
            "Usage: `/edit <id> <field> <value>`\n"
            "Fields: `category`, `amount`, `note`, `account` (bank/cash)\n"
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
    elif field in ("acc", "account", "account_type"):
        update_kwargs["account_type"] = "cash" if "cash" in value.lower() else "bank"
    elif field in ("type", "trans_type"):
        if value.lower() not in ("expense", "income"):
            await update.message.reply_text("Type must be 'expense' or 'income'.")
            return
        update_kwargs["trans_type"] = value.lower()
    else:
        await update.message.reply_text("Valid fields: `category`, `amount`, `note`, `account`, `type`", parse_mode="Markdown")
        return

    success = database.update_transaction(user_id, tx_id, **update_kwargs)
    if success:
        updated = database.get_transaction_by_id(user_id, tx_id)
        await update.message.reply_text(
            f"✅ Transaction #{tx_id} updated!\n"
            f"• Type: {updated['trans_type'].upper()}\n"
            f"• Amount: {updated['amount']:,.2f}\n"
            f"• Category: {updated['category']}\n"
            f"• Account: {updated.get('account_type', 'bank').upper()}\n"
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
    elif data.startswith("setacc_"):
        parts = data.split("_", 2)
        tx_id = int(parts[1])
        acc_type = parts[2]
        if database.update_transaction(user_id, tx_id, account_type=acc_type):
            await query.edit_message_text(
                f"{query.message.text}\n\n💳 *Account changed to:* {acc_type.upper()}",
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
            trans_datetime=slip.trans_datetime,
            account_type="bank"
        )

        reply = (
            f"✅ *Recorded successfully!*\n\n"
            f"🆔 ID: #{tx_id}\n"
            f"💵 Amount: `{slip.amount:,.2f} {slip.currency or 'THB'}`\n"
            f"🏷️ Category: {slip.category}\n"
            f"📝 Note: {slip.raw_note or 'None'}\n"
            f"👤 To: {slip.receiver or 'Unknown'}\n"
            f"📅 Date: {slip.trans_datetime}\n"
            f"💳 Source: BANK ACCOUNT\n\n"
            f"_Tap buttons below to change category/account or delete:_"
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
    
    # Check for help button
    if user_text in ("❓ Help", "help", "/help"):
        await start_command(update, context)
        return

    try:
        reply = queries.answer_user_query(user_id, user_text)
        await update.message.reply_text(
            reply,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown"
        )
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
    app.add_handler(CommandHandler("setbalance", set_balance_command))
    app.add_handler(CommandHandler("cash", cash_command))
    app.add_handler(CommandHandler("edit", edit_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling()

if __name__ == "__main__":
    main()
