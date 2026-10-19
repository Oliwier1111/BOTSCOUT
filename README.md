# SCOUT – AI agent do szukania stron internetowych

Agent szuka **lokalnych, prywatnych firm** (dentysta w Poznaniu, komornik w Komornikach, mechanik, fryzjer, kancelaria…),
odrzuca sieciówki i duże marki (Media Expert, Lux Med, Biedronka itd.) i wybiera te, którym warto zaproponować nową stronę:

| Typ leada | Co znaczy |
|---|---|
| **BRAK_STRONY** | firma jest w Google Maps, ale nie ma strony |
| **TYLKO_SOCIAL** | zamiast strony ma tylko Facebooka, Instagram, Booksy, ZnanyLekarz itp. |
| **STRONA_NIE_DZIALA** | link prowadzi do martwej, zaparkowanej lub zawieszonej domeny |
| **SLABA_STRONA** | strona jest, ale przestarzała, brzydka, bez wersji mobilnej, wolna, bez HTTPS itd. |

Każda firma dostaje **wynik 0–100** (im wyżej, tym lepszy lead), priorytet **A/B/C**, listę konkretnych powodów
po polsku i gotowe zdanie na pierwszy kontakt. Wyniki trafiają do `wyniki/` jako:
- `leady_*.csv` – otwiera się w Excelu / Google Sheets (separator `;`, polskie znaki działają),
- `leady_*.json` – dla panelu agentów,
- `leady_*.html` – czytelny raport w ciemnym stylu panelu.

Agent **nikogo sam nie kontaktuje** – tylko przygotowuje listę dla handlowca.

---

## Jak to działa

1. **Wyszukiwanie** – Google Places API (zapytania typu „dentysta Poznań”). Bez klucza Google: darmowy OpenStreetMap (mniej telefonów i stron).
2. **Filtr sieciówek** – lista `data/sieciowki.txt` + firmy z ogromną liczbą opinii + ta sama domena u wielu placówek.
3. **Audyt techniczny strony** – czy działa, HTTPS i certyfikat, wersja mobilna, rok w stopce (©2011), stary WordPress/Joomla,
   FrontPage, Flash, tabelki, `<font>`, stary jQuery, tytuł/opis/H1, klikalny telefon, formularz, „strona w budowie”,
   zaparkowana domena. Przy okazji zbiera **e-maile, telefony i linki social** (także z podstrony „Kontakt”).
4. **Inne kanały kontaktu** – dla firm **bez strony, ale z telefonem** agent szuka w wyszukiwarce (DuckDuckGo, za darmo)
   e-maila, Facebooka, Instagrama i profili na portalach (ZnanyLekarz, Booksy, Panorama Firm…). Wynik przyjmuje tylko,
   gdy zgadza się telefon albo nazwa i miasto (kolumna `kontakty_uwagi` mówi, które). Przy okazji wyłapuje firmy,
   które jednak mają stronę (ten sam telefon na stronie) – te są audytowane jak każda inna strona.
   Kontakty zapisane w OpenStreetMap (`email`, `contact:facebook`…) są brane zawsze. Wyłączenie: `--bez-kontaktow`
   albo `SCOUT_KONTAKTY=0`; limit firm na uruchomienie: `SCOUT_KONTAKTY_MAX` (domyślnie 80, ok. 2–5 s na firmę).
5. **Google PageSpeed** – wynik szybkości na telefonie, SEO i dostępność.
6. **Ocena wyglądu przez AI** – Playwright robi zrzut strony (telefon + komputer), Claude ocenia design 1–10,
   wypisuje problemy widoczne na zrzucie i pisze zdanie do handlowca. Robi to tylko dla najlepiej rokujących stron
   (domyślnie 25) i pilnuje budżetu `SCOUT_BUDGET_USD`.
7. **Ranking i eksport.** W CSV są kolumny `email`, `facebook`, `instagram`, `inne_kontakty`, `kontakty_uwagi`.

Wyniki audytów i znalezione kontakty są zapamiętywane na 7 dni (`data/cache_audytow.json`, `data/cache_kontaktow.json`), więc kolejne uruchomienia są szybsze.

---

## Najszybszy start: aplikacja w przeglądarce

1. Zainstaluj **Python 3.10+** z [python.org](https://www.python.org/downloads/) (na Windowsie zaznacz „Add python.exe to PATH”).
2. Kliknij dwa razy **`start.bat`** (Windows) albo **`start.command`** (Mac).
   Za pierwszym razem instalują się pakiety i przeglądarka do zrzutów (kilka minut).
3. Otworzy się strona **http://127.0.0.1:8010**. Kliknij „Klucze API”, wklej klucze, wpisz miasto, zaznacz branże i kliknij **Szukaj**.
4. Przy każdej firmie widać wynik, listę „Co do zmiany”, zrzuty strony z telefonu i komputera, kontakt i zdanie do skopiowania.
   Przycisk „CSV / Excel” pobiera całą listę.

Aplikacja działa tylko na Twoim komputerze; zamknięcie czarnego okna ją wyłącza.

## Instalacja ręczna (Windows / Mac / Linux)

Potrzebny **Python 3.10+**.

```bash
cd "Ai agent do szukania stron internetowych"
python -m venv .venv
# Windows:  .venv\Scripts\activate      Mac/Linux:  source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium     # przeglądarka do zrzutów ekranu (jednorazowo)
copy .env.example .env                    # Mac/Linux: cp .env.example .env
```

Potem uzupełnij klucze w pliku `.env`.

### Test bez kluczy i internetu

```bash
python main.py --mock
```

### Prawdziwe wyszukiwanie

```bash
python main.py --miasto Poznań --branze "dentysta,fizjoterapeuta,kancelaria prawna"
python main.py --miasto Komorniki --miasto Luboń --miasto Swarzędz --branze "komornik,mechanik samochodowy"
python main.py --miasto Poznań --bez-ai --bez-pagespeed      # szybko i za darmo (tylko audyt techniczny)
python main.py --miasto Poznań --min-wynik 60 --ai-top 40     # tylko mocne leady, więcej ocen AI
```

Bez `--branze` agent przechodzi przez ~20 typowych lokalnych branż (lista w `scout_config.py`).

---

## Tryb darmowy (domyślnie włączony)

Przełącznik **„Tylko darmowe limity”** w aplikacji (albo `SCOUT_TYLKO_DARMOWE=1` w `.env`) sprawia, że agent
nic nie kosztuje: firmy szuka w OpenStreetMap, sprawdza strony sam i używa darmowego Google PageSpeed.
Google Places i ocena wyglądu przez AI są wtedy wyłączone, nawet jeśli wpiszesz ich klucze.
Gdy zechcesz płatne usługi, odznacz przełącznik i dopisz klucze.

### Darmowy klucz PageSpeed (bez karty płatniczej)

1. Wejdź na [console.cloud.google.com](https://console.cloud.google.com/) i zaloguj się kontem Google.
2. Na górze kliknij wybór projektu → **Nowy projekt** → nazwa np. `scout` → Utwórz.
3. Menu → **Interfejsy API i usługi → Biblioteka** → wyszukaj **PageSpeed Insights API** → **Włącz**.
4. **Interfejsy API i usługi → Dane logowania → Utwórz dane logowania → Klucz interfejsu API**. Skopiuj klucz.
5. Kliknij klucz → **Ograniczenia interfejsów API → Ogranicz klucz** → zaznacz tylko **PageSpeed Insights API** → Zapisz.
6. **Nie dodawaj konta rozliczeniowego (karty).** Bez niego żadna płatna usługa Google nie zadziała, więc nie da się nic naliczyć.
   Darmowy limit PageSpeed to ok. 25 000 sprawdzeń dziennie; w **Limity i przydziały** można go jeszcze obniżyć.
7. W aplikacji SCOUT kliknij „Klucze API”, wklej klucz w pole PageSpeed i zapisz.

### Później: przejście na płatne

1. W tym samym projekcie Google dodaj konto rozliczeniowe, włącz **Places API (New)** i dopisz je do ograniczeń klucza
   (albo utwórz osobny klucz). W **Budżety i alerty** ustaw np. 20 zł miesięcznie z powiadomieniem.
2. Klucz Anthropic utwórz na [console.anthropic.com](https://console.anthropic.com/) i ustaw tam limit wydatków.
3. W aplikacji wklej klucze i odznacz „Tylko darmowe limity”.

## Potrzebne klucze API

| Klucz | Do czego | Skąd | Koszt |
|---|---|---|---|
| `GOOGLE_PLACES_API_KEY` | szukanie firm w Google Maps (telefon, strona, opinie) | [Google Cloud Console](https://console.cloud.google.com/) → utwórz projekt → włącz **Places API (New)** → Dane logowania → Utwórz klucz API. Wymaga podpięcia karty (billing). | Google daje darmowy miesięczny limit; Text Search to ok. kilka centów za zapytanie po jego przekroczeniu. Ustaw limit w konsoli. |
| `PAGESPEED_API_KEY` | szybkość/SEO na telefonie | ta sama konsola → włącz **PageSpeed Insights API** (może być ten sam klucz) | darmowe (limit ok. 25 000 zapytań dziennie). **Bez klucza Google prawie zawsze odrzuca zapytania.** |
| `BRAVE_API_KEY` (opcjonalny) | szukanie kontaktów przez Brave Search API zamiast DuckDuckGo, tylko poza trybem darmowym | [api-dashboard.search.brave.com](https://api-dashboard.search.brave.com/) | sprawdź aktualny cennik; bez klucza agent używa darmowego DuckDuckGo |
| `ANTHROPIC_API_KEY` | ocena wyglądu strony ze zrzutu | [console.anthropic.com](https://console.anthropic.com/) → API Keys | ok. 1–3 centy za stronę; limit ustawiasz w `SCOUT_BUDGET_USD` |

Bez żadnego klucza agent i tak działa: szuka w OpenStreetMap i robi pełny audyt techniczny.

### Pakiety (`requirements.txt`)

| Pakiet | Po co |
|---|---|
| `requests`, `beautifulsoup4` | pobieranie i analiza stron |
| `python-dotenv` | czytanie kluczy z `.env` |
| `playwright` + `python -m playwright install chromium` | zrzuty ekranu stron |
| `anthropic` | ocena wyglądu przez Claude |
| `fastapi`, `uvicorn` | tylko jeśli panel ma rozmawiać z agentem przez HTTP (`server.py`) |

---

## Ustawienia (`.env` lub `scout_config.py`)

- `SCOUT_MODE` – `real` / `mock` (jak w panelu).
- `SCOUT_BUDGET_USD` – maksymalny wydatek na Claude w jednym uruchomieniu (odpowiednik `TOTAL_BUDGET_USD`).
- `SCOUT_CLAUDE_MODEL` – domyślnie `claude-opus-5-5`; tańsza opcja: `claude-sonnet-5-5`.
- `SCOUT_MAX_PER_QUERY` – ile firm na zapytanie (max 60).
- `SCOUT_MIN_SCORE` – próg wyniku, od którego firma trafia do listy.
- `data/sieciowki.txt` – dopisz marki, które mają być pomijane.
- Nowe branże dla OpenStreetMap dopisuje się w `OSM_TAGS` w `scout/discovery.py` (dla Google wystarczy wpisać nazwę).

---

## Maile do firm (automat)

Po każdym wyszukiwaniu SCOUT przygotowuje dla każdej firmy z adresem e-mail gotowy mail (szablon: `data/szablon_maila.txt`,
możesz go edytować; `{nazwa}`, `{powod}`, `{podpis}`). W panelu, w sekcji „Maile do firm”, przeglądasz szkice i klikasz
„Wyślij zaznaczone” albo włączasz **„Wysyłaj automatycznie”** – wtedy agent sam wysyła w dni robocze w godzinach
`SCOUT_MAILE_GODZINY` (domyślnie 9-17), maksymalnie `SCOUT_MAILE_DZIENNIE` (domyślnie 20) dziennie, z przerwą między mailami.

1. W `.env` wpisz dane swojej skrzynki: `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` (zwykle „hasło aplikacji”), `MAIL_FROM`,
   `MAIL_FROM_NAME`, `MAIL_COMPANY`. Bez `SMTP_HOST` agent tylko przygotowuje szkice.
2. Wbudowane hamulce: jeden mail na adres (nigdy drugi raz), lista „nie pisz” (`data/wypisani.txt`, w panelu), każdy mail
   ma podpis z danymi nadawcy, prośbę o odpisanie „STOP” i nagłówek List-Unsubscribe. Agent nie czyta Twojej skrzynki, więc
   gdy ktoś odpisze „STOP”, dopisz jego adres (albo @domenę) w panelu.
3. **Pamiętaj o prawie:** w Polsce wysyłanie ofert e-mailem do firm wymaga wcześniejszej zgody odbiorcy (Prawo komunikacji
   elektronicznej), a dane osobowe właścicieli podlegają RODO. Szablon prosi o zgodę na przesłanie propozycji, ale
   odpowiedzialność za wysyłkę jest Twoja.

## Automatyczne aktualizacje

W `.env` wpisz `SCOUT_UPDATE_URL=` z adresem zipa z najnowszą wersją (np. repozytorium GitHub:
`https://github.com/<konto>/<repo>/archive/refs/heads/main.zip`). Wtedy `start.bat` / `start.command` sprawdzają
nową wersję przy każdym uruchomieniu, a działający panel robi to co godzinę. Ręcznie: `update.bat`
(albo `python update.py`). Aktualizacja podmienia tylko kod; klucze `.env`, wyniki i pamięć podręczna zostają.

## Wpięcie w panel agentów

Zobacz [`integracja/INTEGRACJA.md`](integracja/INTEGRACJA.md).

## Wrzucenie na GitHub

```bash
git init && git add . && git commit -m "SCOUT - agent do szukania stron"
git remote add origin https://github.com/<twoje-konto>/scout-agent.git
git push -u origin main
```

`.gitignore` pilnuje, żeby `.env` z kluczami i wyniki z danymi firm nie trafiły do repozytorium.

---

## Ważne przy kontaktowaniu firm

Od listopada 2024 w Polsce obowiązuje **Prawo komunikacji elektronicznej**: wysyłanie niezamówionej oferty
e-mailem lub SMS-em i dzwonienie z ofertą wymaga wcześniejszej zgody odbiorcy (dotyczy też firm).
Bezpieczniej jest w pierwszym kontakcie zapytać o zgodę na przedstawienie oferty, a dane z listy traktować
zgodnie z RODO (tylko dane firmowe, możliwość usunięcia na prośbę).
