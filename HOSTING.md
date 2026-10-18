# SCOUT w przeglądarce bez instalacji (Render.com, za darmo)

1. Załóż konto na [github.com](https://github.com) i wrzuć tam ten folder jako repozytorium
   (albo poproś Claude'a, żeby to zrobił, gdy podłączysz GitHuba).
2. Załóż konto na [render.com](https://render.com) (logowanie przez GitHuba). Karta nie jest potrzebna.
3. W Renderze: **New → Blueprint** → wybierz repozytorium → Render przeczyta `render.yaml`.
4. Wpisz **SCOUT_HASLO** – hasło do panelu (żeby nikt obcy nie korzystał z Twojego agenta). Kliknij **Apply**.
5. Po kilku minutach dostaniesz link w stylu `https://scout-xxxx.onrender.com`. Przy wejściu przeglądarka
   zapyta o login (wpisz cokolwiek) i hasło (to z punktu 4).

Uwagi:
- Darmowy plan usypia aplikację po 15 minutach bez ruchu – pierwsze wejście potem trwa ok. minuty.
- Wyniki i klucze wpisane w panelu znikają przy ponownym uruchomieniu. Pobieraj CSV od razu,
  a stałe klucze wpisz w Renderze: **Environment** → `PAGESPEED_API_KEY` itd.
- Ocena wyglądu przez AI (zrzuty ekranu) jest na darmowym planie wyłączona.
