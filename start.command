#!/bin/bash
# SCOUT – start na Macu / Linuxie (dwuklik w Finderze albo ./start.command)
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Zainstaluj Pythona 3.10+ z https://www.python.org/downloads/"; read -r; exit 1; }
if [ ! -d .venv ]; then
  echo "Pierwsze uruchomienie: instaluję pakiety, to potrwa kilka minut..."
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  python -m playwright install chromium
else
  source .venv/bin/activate
fi
[ -f .env ] || cp .env.example .env
python update.py
python server.py
