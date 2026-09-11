import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any

DB_PATH = "expenses.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                trans_type TEXT NOT NULL, -- 'expense' or 'income'
                amount REAL NOT NULL,
                category TEXT DEFAULT 'uncategorized',
                raw_note TEXT,
                sender TEXT,
                receiver TEXT,
                bank TEXT,
                account_type TEXT DEFAULT 'bank', -- 'bank' or 'cash'
                trans_datetime TEXT NOT NULL, -- ISO 8601 string: YYYY-MM-DD HH:MM:SS
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Ensure account_type column exists if table was created previously
        try:
            conn.execute("ALTER TABLE transactions ADD COLUMN account_type TEXT DEFAULT 'bank'")
        except sqlite3.OperationalError:
            pass

        # Table for initial balances
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_balances (
                user_id INTEGER PRIMARY KEY,
                initial_bank REAL DEFAULT 0.0,
                initial_cash REAL DEFAULT 0.0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

def set_initial_balance(user_id: int, initial_bank: Optional[float] = None, initial_cash: Optional[float] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT initial_bank, initial_cash FROM user_balances WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        cur_bank = row["initial_bank"] if row else 0.0
        cur_cash = row["initial_cash"] if row else 0.0

        new_bank = initial_bank if initial_bank is not None else cur_bank
        new_cash = initial_cash if initial_cash is not None else cur_cash

        cursor.execute("""
            INSERT INTO user_balances (user_id, initial_bank, initial_cash, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                initial_bank = excluded.initial_bank,
                initial_cash = excluded.initial_cash,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, new_bank, new_cash))
        conn.commit()

def get_balance_summary(user_id: int) -> Dict[str, float]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT initial_bank, initial_cash FROM user_balances WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        initial_bank = float(row["initial_bank"]) if row else 0.0
        initial_cash = float(row["initial_cash"]) if row else 0.0

        # Calculate bank net
        cursor.execute("""
            SELECT 
                COALESCE(SUM(CASE WHEN trans_type = 'income' THEN amount ELSE -amount END), 0.0) as net
            FROM transactions
            WHERE user_id = ? AND (account_type = 'bank' OR account_type IS NULL)
        """, (user_id,))
        bank_net = float(cursor.fetchone()["net"])

        # Calculate cash net
        cursor.execute("""
            SELECT 
                COALESCE(SUM(CASE WHEN trans_type = 'income' THEN amount ELSE -amount END), 0.0) as net
            FROM transactions
            WHERE user_id = ? AND account_type = 'cash'
        """, (user_id,))
        cash_net = float(cursor.fetchone()["net"])

        current_bank = initial_bank + bank_net
        current_cash = initial_cash + cash_net
        total_balance = current_bank + current_cash

        return {
            "initial_bank": initial_bank,
            "initial_cash": initial_cash,
            "bank_balance": current_bank,
            "cash_balance": current_cash,
            "total_balance": total_balance
        }

def add_transaction(user_id: int, trans_type: str, amount: float, category: str, 
                    raw_note: Optional[str], sender: Optional[str], receiver: Optional[str], 
                    bank: Optional[str], trans_datetime: str, account_type: str = "bank") -> int:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO transactions (user_id, trans_type, amount, category, raw_note, sender, receiver, bank, account_type, trans_datetime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, trans_type, amount, category, raw_note, sender, receiver, bank, account_type, trans_datetime))
        conn.commit()
        return cursor.lastrowid

def get_summary(user_id: int, start_date: str, end_date: str, category: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        query = """
            SELECT 
                trans_type,
                category,
                SUM(amount) as total_amount,
                COUNT(*) as count
            FROM transactions
            WHERE user_id = ? 
              AND datetime(trans_datetime) >= datetime(?) 
              AND datetime(trans_datetime) <= datetime(?)
        """
        params = [user_id, start_date, end_date]

        if category:
            query += " AND LOWER(category) = LOWER(?)"
            params.append(category)

        query += " GROUP BY trans_type, category"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        return [dict(r) for r in rows]

def get_total_period(user_id: int, start_date: str, end_date: str, trans_type: str = "expense") -> float:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) as total
            FROM transactions
            WHERE user_id = ? 
              AND trans_type = ?
              AND datetime(trans_datetime) >= datetime(?) 
              AND datetime(trans_datetime) <= datetime(?)
        """, (user_id, trans_type, start_date, end_date))
        row = cursor.fetchone()
        return float(row["total"]) if row else 0.0

def get_recent_transactions(user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, trans_type, amount, category, raw_note, receiver, account_type, trans_datetime
            FROM transactions
            WHERE user_id = ?
            ORDER BY trans_datetime DESC
            LIMIT ?
        """, (user_id, limit))
        return [dict(r) for r in cursor.fetchall()]

def get_transaction_by_id(user_id: int, tx_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM transactions WHERE id = ? AND user_id = ?
        """, (tx_id, user_id))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_last_transaction(user_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def update_transaction(user_id: int, tx_id: int, **fields) -> bool:
    allowed_fields = {"category", "amount", "raw_note", "trans_type", "trans_datetime", "receiver", "account_type"}
    updates = []
    params = []
    for k, v in fields.items():
        if k in allowed_fields and v is not None:
            updates.append(f"{k} = ?")
            params.append(v)
    if not updates:
        return False
    params.extend([tx_id, user_id])
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            UPDATE transactions SET {', '.join(updates)} WHERE id = ? AND user_id = ?
        """, params)
        conn.commit()
        return cursor.rowcount > 0

def delete_transaction(user_id: int, tx_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM transactions WHERE id = ? AND user_id = ?
        """, (tx_id, user_id))
        conn.commit()
        return cursor.rowcount > 0
