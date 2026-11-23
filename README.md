## Technologie

HTML, JavaScript, Tailwind CSS i Vite. Python, FastAPI i psycopg 3.
PostgreSQL, Docker Compose i Nginx.

## Wymagania

Docker Desktop

## Uruchomienie projektu

```text
git clone https://github.com/onlyJakub/miniX.git
cd miniX
```

Skopiuj konfigurację w PowerShell lub CMD.

```text
copy .env.example .env
```

W terminalu macOS/Linux użyj zamiast tego `cp .env.example .env`.
W pliku `.env` uzupełnij `POSTGRES_PASSWORD` i `JWT_SECRET` osobnymi losowymi
wartościami. Każdą wartość możesz wygenerować poniższym poleceniem.

```text
docker run --rm python:3.12-slim-bookworm python -c "import secrets; print(secrets.token_hex(32))"
```

Uruchom aplikację.

```text
docker compose up --build -d
```

Otwórz [http://localhost:8080](http://localhost:8080). Jeśli port jest zajęty,
zmień `MINIX_PORT` w `.env`. Postęp startu sprawdzisz przez `docker compose ps`,
a logi przez `docker compose logs -f`. Przy pierwszym starcie powstaje pełny
schemat bazy. Aktualizację kodu uruchamia to samo polecenie z `--build`.

## Testy

Zainstaluj `backend/requirements-dev.txt`, ustaw `TEST_DATABASE_URL` na osobną bazę
PostgreSQL z prawem `CREATE SCHEMA` i uruchom `python -m unittest discover -s backend -t .`.
Testy tworzą i usuwają własne schematy. Brak adresu bazy oznacza pominięcie testów.
Testy frontendu i build uruchomisz w `frontend` przez `npm test` i `npm run build`.