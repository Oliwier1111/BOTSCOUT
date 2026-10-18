"""Zapis leadów: CSV (Excel), JSON (dla panelu) i raport HTML do przeglądania."""
from __future__ import annotations

import csv
import datetime as dt
import html
import json
from pathlib import Path

from .models import Lead

TYPE_LABEL = {
    "BRAK_STRONY": "Brak strony",
    "TYLKO_SOCIAL": "Tylko social/portal",
    "STRONA_NIE_DZIALA": "Strona nie działa",
    "SLABA_STRONA": "Słaba strona",
    "OK": "Strona OK",
}


def save(leads: list[Lead], out_dir: Path, tag: str = "") -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    base = out_dir / f"leady_{tag + '_' if tag else ''}{stamp}"
    rows = [l.to_row() for l in leads]

    csv_path = base.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig = polskie znaki w Excelu
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
            w.writeheader()
            w.writerows(rows)

    json_path = base.with_suffix(".json")
    json_path.write_text(json.dumps([l.to_dict() for l in leads], ensure_ascii=False, indent=2), encoding="utf-8")

    html_path = base.with_suffix(".html")
    html_path.write_text(_report(leads, stamp), encoding="utf-8")
    return {"csv": csv_path, "json": json_path, "html": html_path}


def _report(leads: list[Lead], stamp: str) -> str:
    e = html.escape
    counts: dict[str, int] = {}
    for l in leads:
        counts[l.lead_type] = counts.get(l.lead_type, 0) + 1
    chips = "".join(f"<span class=chip>{e(TYPE_LABEL[k])}: <b>{v}</b></span>" for k, v in counts.items())
    trs = []
    for l in leads:
        b, a = l.business, l.audit
        site = f'<a href="{e(b.website)}" target=_blank>{e(b.website[:45])}</a>' if b.website else "—"
        maps = f' · <a href="{e(b.maps_url)}" target=_blank>mapa</a>' if b.maps_url else ""
        mail = ", ".join(e(x) for x in list(dict.fromkeys(b.emails + (a.emails if a else [])))[:2])
        extra = [f'<a href="{e(u)}" target=_blank>{e(u.split("/")[2])}</a>' for u in b.socials[:3]]
        extra += [f'<a href="{e(c.split(": ", 1)[-1])}" target=_blank>{e(c.split(": ", 1)[0])}</a>' for c in b.other_contacts]
        mail += (" · " + " · ".join(extra)) if extra else ""
        trs.append(
            f"<tr><td class='sc t{l.tier}'>{l.score}<small>{l.tier}</small></td>"
            f"<td><b>{e(b.name)}</b><br><small>{e(b.category)} · {e(b.address)}</small></td>"
            f"<td>{e(TYPE_LABEL.get(l.lead_type, l.lead_type))}</td>"
            f"<td>{site}{maps}<br><small>{e(b.phone)} {mail}</small>"
            + (f"<br><small>właściciel: {e(b.owner_name)}</small>" if b.owner_name else "") + "</td>"
            f"<td><ul>{''.join('<li>' + e(r) + '</li>' for r in l.reasons[:6])}</ul>"
            f"<i>{e(l.pitch)}</i></td></tr>"
        )
    return f"""<!doctype html><html lang=pl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>SCOUT – leady {stamp}</title>
<style>
body{{font:14px/1.45 system-ui,sans-serif;background:#0b1114;color:#d7e3e7;margin:0;padding:16px}}
h1{{font-size:18px;letter-spacing:.2em;color:#5fe3b0}}a{{color:#6fc3ff}}
.chip{{display:inline-block;border:1px solid #234;padding:3px 9px;margin:0 6px 6px 0;border-radius:4px}}
table{{border-collapse:collapse;width:100%}}td{{border-top:1px solid #1d2a30;padding:8px;vertical-align:top}}
small{{color:#8aa}}ul{{margin:0 0 4px;padding-left:18px}}i{{color:#b9c9a0}}
.sc{{font-size:20px;font-weight:700;text-align:center;width:56px}}.sc small{{display:block;font-size:11px}}
.tA{{color:#5fe3b0}}.tB{{color:#f0c060}}.tC{{color:#889}}
@media(max-width:700px){{td{{display:block;border:0}}tr{{display:block;border-top:1px solid #234}}}}
</style></head><body><h1>SCOUT · LEADY · {stamp}</h1><p>{chips}</p>
<table>{''.join(trs)}</table></body></html>"""
