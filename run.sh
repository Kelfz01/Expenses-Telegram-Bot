#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "Installing required libraries..."
pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "Created .env file! Please edit telegram-expense-bot/.env with:"
    echo "  1. TELEGRAM_BOT_TOKEN (from @BotFather)"
    echo "  2. GEMINI_API_KEY (from https://aistudio.google.com/apikey)"
    echo ""
    exit 0
fi

echo "Starting bot..."
python3 bot.py
