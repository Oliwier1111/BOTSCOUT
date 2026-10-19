"""Konfiguracja agenta SCOUT (szukanie lokalnych firm ze słabymi stronami).

Wszystkie klucze czytane są ze zmiennych środowiskowych lub z pliku .env
(skopiuj .env.example -> .env i uzupełnij).
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
except ImportError:  # python-dotenv jest opcjonalny
    pass

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = Path(os.getenv("SCOUT_OUTPUT_DIR", BASE_DIR / "wyniki"))

# --- Identyfikacja agenta w panelu ---------------------------------------
AGENT_NAME = "SCOUT"
AGENT_ROLE = "Łowca klientów / audyt stron"
AGENT_SECTION = "RESEARCH / ANALIZA"

# --- Tryb pracy -------------------------------------------------------------
# "real" = prawdziwe API i strony, "mock" = dane testowe bez internetu i kosztów
MODE = os.getenv("SCOUT_MODE", "real").lower()

# Tylko darmowe limity: szukanie w OpenStreetMap, PageSpeed (darmowy klucz bez płatności),
# bez Google Places i bez oceny AI – nawet jeśli te klucze są wpisane. 0 = włącz płatne usługi.
FREE_ONLY = os.getenv("SCOUT_TYLKO_DARMOWE", "1") == "1"

# --- Klucze API ----------------------------------------------------------
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
PAGESPEED_API_KEY = os.getenv("PAGESPEED_API_KEY", "") or GOOGLE_PLACES_API_KEY
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Opcjonalnie: Brave Search API do szukania kontaktów (bez klucza – darmowe DuckDuckGo).
# Używane tylko poza trybem darmowym.
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")

# --- Budżet (tylko płatne wywołania AI; Google liczy osobno w swojej konsoli)
TOTAL_BUDGET_USD = float(os.getenv("SCOUT_BUDGET_USD", "2.00"))

# --- Model Claude do oceny wizualnej -------------------------------------
CLAUDE_MODEL = os.getenv("SCOUT_CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.getenv("SCOUT_CLAUDE_EFFORT", "medium")
# Cennik USD za 1M tokenów (wejście, wyjście) do liczenia budżetu
CLAUDE_PRICING = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

# --- Domyślne wyszukiwanie ----------------------------------------------
DEFAULT_CITIES = ["Poznań"]
DEFAULT_CATEGORIES = [
    "dentysta",
    "gabinet stomatologiczny",
    "fizjoterapeuta",
    "weterynarz",
    "kancelaria prawna",
    "biuro rachunkowe",
    "komornik",
    "notariusz",
    "mechanik samochodowy",
    "hydraulik",
    "elektryk",
    "fryzjer",
    "salon kosmetyczny",
    "restauracja",
    "piekarnia",
    "kwiaciarnia",
    "szkoła językowa",
    "psycholog",
    "optyk",
    "firma remontowa",
]

# Segment: "duze" = większe firmy (B2B, sp. z o.o., producenci, hurtownie…), "male" = lokalne gabinety i usługi.
SEGMENT = os.getenv("SCOUT_SEGMENT", "duze").lower()
DEFAULT_CATEGORIES_DUZE = [
    "biura firm",
    "producent",
    "zakład produkcyjny",
    "hurtownia",
    "firma budowlana",
    "deweloper",
    "firma transportowa",
    "spedycja",
    "firma logistyczna",
    "salon samochodowy",
    "hotel",
    "centrum medyczne",
    "klinika",
    "biuro nieruchomości",
    "firma IT",
    "agencja pracy",
    "producent mebli",
    "okna i drzwi",
    "firma instalacyjna",
    "maszyny i urządzenia",
    "szkoła prywatna",
]
DEFAULT_CATEGORIES_MALE = DEFAULT_CATEGORIES


def categories_for(segment: str | None = None) -> list[str]:
    return DEFAULT_CATEGORIES_DUZE if (segment or SEGMENT) == "duze" else DEFAULT_CATEGORIES_MALE


# Rejestry (Biała lista VAT + KRS): forma prawna, kapitał, PKD, wiek firmy – darmowe, bez klucza
USE_REGISTRY = os.getenv("SCOUT_REJESTRY", "1") == "1"
REGISTRY_MAX = int(os.getenv("SCOUT_REJESTRY_MAX", "80"))

# Twardy dzienny limit zapytań do Google Places (jedno zapytanie ≈ kilka groszy). 150 ≈ kilkanaście zł dziennie przy maksymalnym użyciu.
PLACES_DAILY_LIMIT = int(os.getenv("SCOUT_PLACES_DZIENNIE", "150"))

# Ile wyników z Google na jedno zapytanie (max 60 = 3 strony po 20)
MAX_RESULTS_PER_QUERY = int(os.getenv("SCOUT_MAX_PER_QUERY", "40"))

# --- Filtrowanie sieciówek ---------------------------------------------
# Firma z większą liczbą opinii najczęściej jest siecią lub dużą marką
MAX_REVIEWS_FOR_LOCAL = int(os.getenv("SCOUT_MAX_REVIEWS", "1500"))
# W segmencie "duze" dużo opinii to zaleta, nie sieciówka (sieci i tak odsiewa lista + wspólna domena)
MAX_REVIEWS_DUZE = int(os.getenv("SCOUT_MAX_REVIEWS_DUZE", "20000"))
# Ta sama domena u tylu firm w wynikach => sieć
CHAIN_DOMAIN_THRESHOLD = 3

# --- Audyt stron -------------------------------------------------------
REQUEST_TIMEOUT = 15
AUDIT_WORKERS = int(os.getenv("SCOUT_WORKERS", "6"))
USE_PAGESPEED = os.getenv("SCOUT_PAGESPEED", "1") == "1"
USE_VISUAL_AI = os.getenv("SCOUT_VISUAL_AI", "1") == "1"
CACHE_DAYS = 7
# Firmy bez strony, ale z telefonem: szukaj e-maila, Facebooka, Instagrama i portali (scout/contacts.py)
FIND_CONTACTS = os.getenv("SCOUT_KONTAKTY", "1") == "1"
# Maks. tyle firm na jedno uruchomienie (każda to 1–2 zapytania do wyszukiwarki, ok. 2 s przerwy)
CONTACTS_MAX = int(os.getenv("SCOUT_KONTAKTY_MAX", "80"))
# Własna ścieżka do Chrome/Chromium (zwykle puste – Playwright używa swojej)
CHROMIUM_PATH = os.getenv("SCOUT_CHROMIUM_PATH", "")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 SparkScaleScout/1.0"
)

# Minimalny wynik szansy (0-100), żeby firma trafiła do listy leadów
MIN_LEAD_SCORE = int(os.getenv("SCOUT_MIN_SCORE", "35"))

# --- Wysyłka maili (scout/outreach.py) ---------------------------------
# Bez SMTP_HOST agent tylko PRZYGOTOWUJE szkice (wyniki/ i panel); nic nie wysyła.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_TLS = os.getenv("SMTP_TLS", "starttls")  # starttls | ssl | none
MAIL_FROM = os.getenv("MAIL_FROM", "") or SMTP_USER
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "")
MAIL_REPLY_TO = os.getenv("MAIL_REPLY_TO", "")
MAIL_COMPANY = os.getenv("MAIL_COMPANY", "")  # nazwa Twojej firmy w podpisie
MAIL_SIGNATURE = os.getenv("MAIL_SIGNATURE", "")  # dodatkowe linie podpisu, np. telefon; \n = nowa linia
# 1 = po każdym wyszukiwaniu agent sam wysyła (w limicie i w godzinach). Domyślnie 0: wysyłasz zaznaczone w panelu.
OUTREACH_AUTO = os.getenv("SCOUT_AUTOWYSYLKA", "0") == "1"
OUTREACH_DAILY_LIMIT = int(os.getenv("SCOUT_MAILE_DZIENNIE", "20"))
OUTREACH_DELAY_S = int(os.getenv("SCOUT_MAILE_PRZERWA_S", "60"))
OUTREACH_HOURS = os.getenv("SCOUT_MAILE_GODZINY", "9-17")  # tylko dni robocze, czas lokalny (automat)
OUTREACH_MIN_TIER = os.getenv("SCOUT_MAILE_MIN_PRIORYTET", "B")  # A lub B
