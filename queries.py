import os
import time
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, cast
from google import genai
from google.genai import types
from google.genai.errors import APIError
import database

class QueryIntent(BaseModel):
    is_expense_query: bool = Field(description="True if asking about summaries, totals, lists, balance, or asking to change/update/delete a transaction")
    intent_action: str = Field(default="query", description="'query', 'balance', 'update', 'delete', or 'recent'")
    target_tx_id: Optional[int] = Field(default=None, description="Transaction ID if user mentions one to update or delete")
    update_field: Optional[str] = Field(default=None, description="'category', 'amount', 'note', 'account_type', or 'trans_type'")
    update_value: Optional[str] = Field(default=None, description="New value for the field being updated")
    start_date: Optional[str] = Field(description="Start date formatted as 'YYYY-MM-DD 00:00:00'")
    end_date: Optional[str] = Field(description="End date formatted as 'YYYY-MM-DD 23:59:59'")
    category: Optional[str] = Field(description="Filter category if specified (e.g. food, transport, bills), or null if all categories")
    trans_type: str = Field(default="expense", description="'expense', 'income', or 'all'")
    wants_recent_list: bool = Field(default=False, description="True if user specifically asked for recent transactions list")
    wants_balance: bool = Field(default=False, description="True if user asked for current balance, total balance, bank balance, or cash balance")

CANDIDATE_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-latest"]

def format_balance_message(user_id: int) -> str:
    bal = database.get_balance_summary(user_id)
    return (
        "💰 *Current Balance Summary*\n\n"
        f"🏦 *Bank Accounts:* `{bal['bank_balance']:,.2f} THB`\n"
        f"💵 *Cash:* `{bal['cash_balance']:,.2f} THB`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 *Total Balance:* `{bal['total_balance']:,.2f} THB`\n\n"
        f"_Initial set: Bank: {bal['initial_bank']:,.2f} | Cash: {bal['initial_cash']:,.2f}_\n"
        f"_(Use /setbalance to update your starting balance)_"
    )

def answer_user_query(user_id: int, user_question: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "GEMINI_API_KEY not configured."

    # Direct keyword shortcut for instant response
    q_lower = user_question.lower().strip()
    if q_lower in ("💰 current balance", "current balance", "balance", "total balance", "my balance"):
        return format_balance_message(user_id)

    client = genai.Client(api_key=api_key)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S (Day: %A)")

    system_prompt = f"""
Current reference datetime: {now_str}
You are an assistant parsing expense and budget inquiries.
Extract the date range, category filter, balance intent, and actions from the user question.

Rules:
- 'today': start of today 00:00:00 to end of today 23:59:59.
- 'this week': Monday of current week 00:00:00 to Sunday 23:59:59.
- 'this month': 1st day of current month to end of current month.
- 'last month': 1st day of previous month to end of previous month.
- Custom date ranges (e.g., 'from 1st to 15th Aug 2026'): resolve start_date and end_date accurately.
- If asking about current balance, bank balance, or cash balance, set wants_balance=True and intent_action='balance'.
"""

    parsed_data = None
    last_error = None

    for model_name in CANDIDATE_MODELS:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[system_prompt, f"User asked: {user_question}"],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=QueryIntent,
                        temperature=0.0
                    )
                )
                parsed_data = response.parsed
                if parsed_data:
                    break
            except APIError as e:
                last_error = e
                if e.code in (429, 503):
                    time.sleep(1.5)
                    continue
                raise e
            except Exception as e:
                last_error = e
                break
        if parsed_data:
            break

    if not parsed_data:
        if last_error:
            raise last_error
        return ("I didn't quite catch that. You can tap the menu buttons below or ask:\n"
                "- 'Expense Today'\n"
                "- 'Current Balance'\n"
                "- 'How much did I spend this week?'\n"
                "- 'Summary of food expenses this month'\n"
                "- 'Show recent transactions'")

    intent: QueryIntent = cast(QueryIntent, parsed_data)

    if intent.wants_balance or intent.intent_action == "balance":
        return format_balance_message(user_id)

    if not intent.is_expense_query:
        return ("I didn't quite catch that. You can tap the menu buttons below or ask:\n"
                "- 'Expense Today'\n"
                "- 'Current Balance'\n"
                "- 'How much did I spend this week?'\n"
                "- 'Summary of food expenses this month'\n"
                "- 'Show recent transactions'")

    if intent.intent_action == "update" or (intent.update_field and intent.update_value):
        tx_id = intent.target_tx_id
        if not tx_id:
            last_tx = database.get_last_transaction(user_id)
            if last_tx:
                tx_id = last_tx["id"]

        if not tx_id:
            return "No transaction found to update."

        kwargs = {}
        field = intent.update_field.lower() if intent.update_field else "category"
        val = intent.update_value or ""
        if "cat" in field:
            kwargs["category"] = val
        elif "amount" in field:
            try:
                kwargs["amount"] = float(val.replace(",", ""))
            except ValueError:
                return f"Could not parse '{val}' as a valid number."
        elif "note" in field:
            kwargs["raw_note"] = val
        elif "account" in field or "cash" in field or "bank" in field:
            kwargs["account_type"] = "cash" if "cash" in val.lower() else "bank"
        elif "type" in field:
            kwargs["trans_type"] = val.lower()
        else:
            kwargs["category"] = val

        if database.update_transaction(user_id, tx_id, **kwargs):
            updated = database.get_transaction_by_id(user_id, tx_id)
            if updated:
                return (f"✅ Updated Transaction #{tx_id}!\n"
                        f"• Amount: {updated['amount']:,.2f}\n"
                        f"• Category: {updated['category']}\n"
                        f"• Account: {updated.get('account_type', 'bank').upper()}\n"
                        f"• Note: {updated['raw_note'] or 'None'}")
        return f"Could not find or update transaction #{tx_id}."

    if intent.intent_action == "delete":
        tx_id = intent.target_tx_id
        if not tx_id:
            return "Please specify which transaction ID to delete, e.g. 'delete transaction 5'."
        if database.delete_transaction(user_id, tx_id):
            return f"🗑️ Transaction #{tx_id} has been deleted."
        return f"Could not find transaction #{tx_id}."

    if intent.wants_recent_list or intent.intent_action == "recent":
        recent = database.get_recent_transactions(user_id, limit=5)
        if not recent:
            return "No transactions recorded yet."
        lines = ["*Recent Transactions:*"]
        for r in recent:
            note_part = f" ({r['raw_note']})" if r.get('raw_note') else ""
            acc_part = f" [{r.get('account_type', 'bank').upper()}]"
            lines.append(f"• #{r['id']} | {r['trans_datetime'][:10]} | {r['trans_type'].capitalize()}: {r['amount']:,.2f} | {r['category']}{acc_part}{note_part}")
        return "\n".join(lines)

    start = intent.start_date or "2000-01-01 00:00:00"
    end = intent.end_date or datetime.now().strftime("%Y-%m-%d 23:59:59")

    breakdown = database.get_summary(user_id, start, end, category=intent.category)
    if not breakdown:
        cat_msg = f" for '{intent.category}'" if intent.category else ""
        return f"No records found{cat_msg} between {start[:10]} and {end[:10]}."

    total_expense = sum(item["total_amount"] for item in breakdown if item["trans_type"] == "expense")
    total_income = sum(item["total_amount"] for item in breakdown if item["trans_type"] == "income")

    lines = [f"📊 *Summary ({start[:10]} to {end[:10]}):*"]
    if total_expense > 0:
        lines.append(f"\n💸 *Total Expenses:* `{total_expense:,.2f} THB`")
        for item in breakdown:
            if item["trans_type"] == "expense":
                lines.append(f"  • {item['category']}: {item['total_amount']:,.2f} ({item['count']} items)")

    if total_income > 0:
        lines.append(f"\n💵 *Total Income:* `{total_income:,.2f} THB`")
        for item in breakdown:
            if item["trans_type"] == "income":
                lines.append(f"  • {item['category']}: {item['total_amount']:,.2f} ({item['count']} items)")

    return "\n".join(lines)
