"""SCOUT – uruchamianie z linii poleceń.

Przykłady:
  python main.py --miasto Poznań --branze "dentysta,komornik"
  python main.py --miasto Poznań --miasto Komorniki --limit 20 --bez-ai
  python main.py --mock            # test bez internetu i kosztów
"""
import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description="SCOUT: szuka lokalnych firm bez strony lub ze słabą stroną")
    p.add_argument("--miasto", action="append", help="miasto (można podać kilka razy)")
    p.add_argument("--branze", help="lista branż po przecinku, np. 'dentysta,fryzjer'")
    p.add_argument("--limit", type=int, help="maks. firm na jedno zapytanie (domyślnie 40, max 60)")
    p.add_argument("--min-wynik", type=int, help="minimalny wynik leada 0-100 (domyślnie 35)")
    p.add_argument("--bez-ai", action="store_true", help="bez oceny wizualnej przez Claude")
    p.add_argument("--bez-pagespeed", action="store_true", help="bez Google PageSpeed (szybciej)")
    p.add_argument("--ai-top", type=int, default=25, help="ile stron ocenić wizualnie (domyślnie 25)")
    p.add_argument("--bez-kontaktow", action="store_true",
                   help="nie szukaj e-maila/Facebooka/Instagrama dla firm bez strony")
    p.add_argument("--wyslij", action="store_true", help="wyślij przygotowane maile (w dziennym limicie)")
    p.add_argument("--mock", action="store_true", help="tryb testowy z danymi przykładowymi")
    args = p.parse_args()

    if args.mock:
        os.environ["SCOUT_MODE"] = "mock"
    from scout.agent import ScoutAgent  # import po ustawieniu trybu

    agent = ScoutAgent()
    leads = agent.run(
        cities=args.miasto,
        categories=[c.strip() for c in args.branze.split(",")] if args.branze else None,
        max_per_query=args.limit,
        min_score=args.min_wynik,
        visual_ai=False if args.bez_ai else None,
        use_pagespeed=False if args.bez_pagespeed else None,
        visual_top=args.ai_top,
        find_contacts=False if args.bez_kontaktow else None,
    )
    if args.wyslij:
        from scout import outreach
        print(outreach.send(log=print))
    print()
    for l in leads[:15]:
        print(f"{l.score:>3} [{l.tier}] {l.lead_type:<18} {l.business.name[:45]:<45} {l.reasons[0] if l.reasons else ''}")
    st = agent.get_state()
    print("\nPliki:", *st["last_files"].values(), sep="\n  ")


if __name__ == "__main__":
    main()
