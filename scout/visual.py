"""Ocena wyglądu strony: zrzut ekranu (Playwright) + ocena przez Claude (vision).

Ten moduł jest opcjonalny. Bez Playwrighta albo bez ANTHROPIC_API_KEY agent
po prostu pomija ocenę wizualną i opiera się na audycie technicznym.
"""
from __future__ import annotations

import base64
import json
import re
import threading
from pathlib import Path

import scout_config as config
from .models import Audit

PROMPT = """Jesteś doświadczonym projektantem stron WWW i oceniasz stronę lokalnej firmy z Polski
jako potencjalnego klienta agencji, która robi nowe strony i odświeża stare.

Dostajesz dwa zrzuty: wersję na telefon i na komputer. Oceń, jak strona wygląda dla klienta
tej firmy dzisiaj: nowoczesność designu, czytelność na telefonie, typografia, zdjęcia,
spójność kolorów, widoczność telefonu i przycisku kontaktu/rezerwacji, zaufanie.

design_score: 1 = bardzo przestarzała/brzydka, 10 = nowoczesna i dopracowana.
estimated_design_year: rok, na który wygląda design.
issues: 3-6 konkretnych problemów widocznych na zrzutach, po polsku, krótko.
pitch: jedno zdanie po polsku, które handlowiec może powiedzieć właścicielowi,
co konkretnie poprawimy (bez przesady i bez obrażania).
Firma: {name} ({category}), strona: {url}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "design_score": {"type": "integer"},
        "estimated_design_year": {"type": "integer"},
        "mobile_friendly": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "pitch": {"type": "string"},
    },
    "required": ["design_score", "estimated_design_year", "mobile_friendly", "issues", "pitch"],
    "additionalProperties": False,
}


class Budget:
    """Pilnuje TOTAL_BUDGET_USD dla wywołań Claude."""

    def __init__(self, limit_usd: float):
        self.limit = limit_usd
        self.spent = 0.0
        self._lock = threading.Lock()

    def can_spend(self, est: float = 0.03) -> bool:
        with self._lock:
            return self.spent + est <= self.limit

    def add(self, model: str, input_tokens: int, output_tokens: int) -> float:
        pin, pout = config.CLAUDE_PRICING.get(model, (4.0, 20.0))
        cost = input_tokens / 1e6 * pin + output_tokens / 1e6 * pout
        with self._lock:
            self.spent += cost
        return cost


def available() -> tuple[bool, str]:
    if config.FREE_ONLY:
        return False, "tryb tylko darmowy"
    if not config.ANTHROPIC_API_KEY:
        return False, "brak ANTHROPIC_API_KEY"
    try:
        import anthropic  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as e:
        return False, f"brak pakietu ({e.name})"
    return True, ""


def take_screenshots(url: str, out_dir: Path, slug: str) -> tuple[Path, Path]:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    mobile, desktop = out_dir / f"{slug}_mobile.jpg", out_dir / f"{slug}_desktop.jpg"
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=config.CHROMIUM_PATH or None)
        try:
            for path, kw in (
                (mobile, dict(viewport={"width": 390, "height": 844}, is_mobile=True, device_scale_factor=2)),
                (desktop, dict(viewport={"width": 1366, "height": 900})),
            ):
                ctx = browser.new_context(user_agent=config.USER_AGENT, ignore_https_errors=True, locale="pl-PL", **kw)
                page = ctx.new_page()
                page.goto(url, wait_until="load", timeout=30000)
                page.wait_for_timeout(1500)
                page.screenshot(path=str(path), type="jpeg", quality=70)
                ctx.close()
        finally:
            browser.close()
    return mobile, desktop


def _img(path: Path) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(path.read_bytes()).decode()},
    }


def review(a: Audit, name: str, category: str, budget: Budget, log=lambda _: None) -> str:
    """Uzupełnia a.visual_*; zwraca pitch (albo "")."""
    url = a.final_url or a.url
    if not url or not budget.can_spend():
        return ""
    import anthropic

    slug = re.sub(r"[^a-z0-9]+", "-", url.lower().split("://")[-1]).strip("-")[:60]
    try:
        mobile, desktop = take_screenshots(url, config.OUTPUT_DIR / "zrzuty", slug)
    except Exception as e:  # noqa: BLE001 - każda awaria przeglądarki = pomijamy
        log(f"Zrzut ekranu nie udał się dla {url}: {type(e).__name__}")
        return ""
    a.screenshot_path = str(desktop)

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    try:
        resp = client.beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": config.CLAUDE_EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[
                {
                    "role": "user",
                    "content": [
                        _img(mobile),
                        _img(desktop),
                        {"type": "text", "text": PROMPT.format(name=name, category=category, url=url)},
                    ],
                }
            ],
        )
    except anthropic.RateLimitError:
        log("Claude: limit zapytań – pomijam ocenę wizualną tej strony")
        return ""
    except anthropic.APIStatusError as e:
        log(f"Claude: błąd API {e.status_code}")
        return ""
    except anthropic.APIConnectionError:
        log("Claude: brak połączenia")
        return ""

    cost = budget.add(config.CLAUDE_MODEL, resp.usage.input_tokens, resp.usage.output_tokens)
    if resp.stop_reason == "refusal":
        log(f"Claude odmówił oceny {url}")
        return ""
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log(f"Claude: nieczytelna odpowiedź dla {url}")
        return ""
    a.visual_score = max(1, min(10, int(data["design_score"])))
    a.visual_issues = [str(x) for x in data.get("issues", [])][:6]
    a.visual_summary = f"wygląda na ok. {data.get('estimated_design_year')} r."
    if not data.get("mobile_friendly", True):
        a.visual_issues.insert(0, "słabo wygląda na telefonie")
    log(f"Ocena wizualna {name}: {a.visual_score}/10 (koszt ${cost:.3f}, łącznie ${budget.spent:.2f})")
    return str(data.get("pitch", ""))
