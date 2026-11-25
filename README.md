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

## Test połączenia z LM Studio

W LM Studio załaduj model `basal-omni-1.0-mini` w trybie DECISION i uruchom
serwer na porcie `1234`. Z katalogu projektu uruchom diagnostykę.

```text
docker compose up --build -d
docker compose exec -e AI_MODERATION_ENABLED=true backend python -m backend.moderation
```

Polecenie wysyła przykładowy tekst do `/v1/systemone` i wypisuje decyzję,
pewność oraz prawdopodobieństwa. W razie błędu wyświetla komunikat i kończy
się kodem 1. Domyślny adres w Dockerze to `http://host.docker.internal:1234`.
Możesz go zmienić przez `AI_BASE_URL` w głównym `.env`.

Polecenie włącza klasyfikację na czas diagnostyki. Aby włączyć moderację
publikacji, ustaw `AI_MODERATION_ENABLED=true` w głównym pliku `.env` i uruchom
`docker compose up --build -d --wait`.

Przy włączonej moderacji nowe posty są zapisywane wyłącznie po decyzji `safe`.
Jeśli LM Studio jest niedostępne, publikacja zwraca HTTP 503, a formularz
zachowuje szkic do ponownej próby. Domyślne ustawienie
`AI_MODERATION_ENABLED=false` pozwala publikować bez połączenia z LM Studio.

## Testy

Zainstaluj `backend/requirements-dev.txt`, ustaw `TEST_DATABASE_URL` na osobną bazę
PostgreSQL z prawem `CREATE SCHEMA` i uruchom `python -m unittest discover -s backend -t .`.
Testy tworzą i usuwają własne schematy. Brak adresu bazy oznacza pominięcie testów
korzystających z PostgreSQL.
Same testy moderacji uruchomisz przez `python -m unittest backend.test_moderation`.
Korzystają z symulowanych odpowiedzi LM Studio.
Testy frontendu i build uruchomisz w `frontend` przez `npm test` i `npm run build`.