@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SCOUT - agent do szukania stron

rem --- 1. Python (sprawdzamy, czy naprawde dziala, a nie tylko skrot ze Sklepu Microsoft) ---
python --version >nul 2>nul
if errorlevel 1 (
  echo Nie znaleziono Pythona. Probuje zainstalowac go automatycznie...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo Jesli instalacja sie udala, ZAMKNIJ to okno i kliknij start.bat jeszcze raz.
  echo Jesli nie: pobierz Pythona z https://www.python.org/downloads/ i zaznacz "Add python.exe to PATH".
  pause
  exit /b
)

rem --- 2. Pakiety (tylko za pierwszym razem) ---
if not exist .venv\Scripts\python.exe (
  echo Pierwsze uruchomienie: instaluje pakiety, to potrwa kilka minut...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  python -m playwright install chromium
) else (
  call .venv\Scripts\activate.bat
)
if not exist .env copy .env.example .env >nul

rem --- 3. Aktualizacja programu (tylko gdy w .env jest SCOUT_UPDATE_URL) ---
python update.py

rem --- 4. Panel na zywo: http://127.0.0.1:8010 (przegladarka otworzy sie sama) ---
echo.
echo  SCOUT startuje: http://127.0.0.1:8010
echo  NIE ZAMYKAJ TEGO OKNA, dopoki korzystasz z SCOUT-a w przegladarce.
echo.
python server.py
echo.
echo SCOUT sie zatrzymal. Jesli powyzej jest blad, zrob zrzut tego okna i wyslij go.
pause
