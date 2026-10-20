# Разработка

## Что нужно поставить

| Инструмент | Версия | Зачем |
| --- | --- | --- |
| Docker + Compose v2 | актуальная | запуск всего решения одной командой |
| Python | 3.12 (`backend/.python-version`) | бэкенд и AI-часть |
| uv | 0.5 и новее | зависимости и запуск Python-команд |
| Node | 22 (`frontend/.nvmrc`) | фронтенд |

Версии зафиксированы: `backend/uv.lock` и `frontend/package-lock.json` коммитятся, установка идёт строго по ним. Если обновили зависимость — lock-файл едет в том же PR.

## Запуск целиком

Появится с задачей A1-05: `deploy/` с Dockerfile и `compose.yaml` на api, worker, postgres, redis и web. После неё запуск сведётся к двум командам:

```
cp .env.example .env
docker compose up -d --build
```

Пока запускаем по частям.

## Запуск по частям

Бэкенд:

```
cd backend
uv sync --frozen --group dev
uv run uvicorn app.main:app --reload --port 8000
```

Конфигурация читается из `.env` в корне репозитория независимо от рабочей директории.
Переменные окружения имеют приоритет над `.env`; некорректные значения `APP_ENV`,
`APP_PORT` и `LOG_LEVEL` останавливают запуск с ошибкой валидации.
`APP_PORT` и `LOG_LEVEL` доступны в настройках приложения; для запуска Uvicorn через CLI
порт и уровень логирования задаются его флагами `--port` и `--log-level`.

Проверка API: `curl http://localhost:8000/health` или `curl http://localhost:8000/api/health`.
Ответ: `{"status":"ok","env":"local"}`. Это проверка живости API без подключения
к PostgreSQL, Redis и LLM. Swagger UI — `/api/docs`, OpenAPI — `/api/openapi.json`.
HTTP-маршруты подключаются в `app/api/router.py`, приложение создаётся в
`app/main.py` через `create_app()`.

Фронтенд:

```
cd frontend
npm ci
npm run dev
```

Dev-сервер фронтенда проксирует `/api` на `localhost:8000`, так что бэкенд можно держать в Docker, а фронтенд — локально.

## Проверки

Те же команды, что в CI. Прогоняем перед тем, как просить ревью:

```
cd backend
uv run ruff format .
uv run ruff check .
uv run mypy app
uv run pytest

cd ../frontend
npm run lint
npm run typecheck
npm run build
```

## Стиль кода

- Python: ruff как форматтер и линтер, строка до 100 символов, mypy в строгом режиме. Типы на публичных функциях обязательны.
- TypeScript: strict, без `any` в коде, который переживёт хакатон. Импорты через алиас `@/`.
- Комментариев в коде не пишем: имя функции и типы объясняют, что происходит. Исключение — docstring у эндпоинта, который попадает в OpenAPI.
- Сообщения в логах и тексты интерфейса — по-русски, имена сущностей и полей — как в данных кейса.

## Переменные окружения

Любая новая переменная сразу появляется в `.env.example` с пустым или безопасным значением и в `app/core/config.py`. Реальные значения живут только в локальном `.env` и в секретах стенда — в репозиторий они не попадают никогда.
