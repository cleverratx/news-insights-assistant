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

Из корня репозитория:

```
cp .env.example .env
docker compose up -d --build --wait
```

Интерфейс — `http://localhost:8080`, Swagger — `http://localhost:8080/api/docs`.
Сервисы, конфигурация и остановка описаны в [../deploy/README.md](../deploy/README.md).

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
Команда `uv run python -m app` применяет `APP_PORT` и `LOG_LEVEL` автоматически.
При локальном запуске без Docker задайте `DATABASE_URL` и `REDIS_URL` с доступными
с хоста адресами и `DATA_DIR=../data`: шаблон `.env.example` настроен для сети Compose.

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

Dev-сервер фронтенда проксирует `/api` на `localhost:8000`, так что бэкенд можно держать в Docker, а фронтенд — локально. Другой адрес бэкенда задаётся `API_PROXY_TARGET`. Список организаций в шапке по умолчанию — демо-компании из [demo-companies.md](demo-companies.md); на синтетических выгрузках из `data/samples` его меняет `VITE_DEMO_COMPANIES`:

```
API_PROXY_TARGET=http://localhost:8001 VITE_DEMO_COMPANIES=U_900001,U_900002,U_900003 npm run dev
```

Типы ответов API лежат в `frontend/src/shared/api/schema.d.ts` и генерируются из OpenAPI бэкенда. После изменения контрактов их пересобирают при запущенном бэкенде:

```
npx openapi-typescript http://localhost:8000/api/openapi.json -o src/shared/api/schema.d.ts
```

## Проверки

Те же команды, что в CI. Прогоняем перед тем, как открыть PR:

```
cd backend
uv run ruff format . ../scripts
uv run ruff check . ../scripts
uv run mypy app
uv run pytest

cd ../frontend
npm run lint
npm run typecheck
npm test
npm run build
```

## Дополнительные проверки

Переключатель демо-компании (A6-08): сценарий сравнения лент, настройка компаний
и правила переходов описаны в
[frontend/src/features/demo/README.md](../frontend/src/features/demo/README.md).

Фильтры ленты (A6-07): поведение, параметры API и сценарии проверки описаны в
[frontend/src/features/feed/README.md](../frontend/src/features/feed/README.md).

Уточняющий диалог по инсайту (A6-06) использует поле `focus` существующего API разборов.
Как открыть чат, какие данные сохраняются в контексте и как проверить его без платных
вызовов LLM — в [frontend/src/features/ask/README.md](../frontend/src/features/ask/README.md).

Интеграционные тесты бюджета требуют `TEST_REDIS_URL` отдельного тестового Redis;
без него они пропускаются. В CI Redis запускается автоматически, поэтому эти тесты
выполняются в каждом PR. Так же устроен тест загрузчика выгрузок: ему нужен
`TEST_DATABASE_URL` тестового PostgreSQL, в CI он поднимается рядом с Redis. Тест
работает в отдельной временной схеме и удаляет её за собой. Проверка готового Docker-стека без платных запросов LLM:

```bash
python3 scripts/check_stack.py
```

Скрипт проверяет API, nginx, OpenAPI, Swagger, JS-файл фронтенда, маршрутизацию SPA,
подключение к PostgreSQL и прохождение задачи через worker. Для нестандартных портов
доступны `--api-url` и `--web-url`. Отдельная CI-проверка `deployment` собирает
и запускает пять сервисов с `.env.example`, затем выполняет этот сценарий.

## Стиль кода

- Python: ruff как форматтер и линтер, строка до 100 символов, mypy в строгом режиме. Типы на публичных функциях обязательны.
- TypeScript: strict, без `any` в коде, который переживёт хакатон. Импорты через алиас `@/`.
- Комментариев в коде не пишем: имя функции и типы объясняют, что происходит. Исключение — docstring у эндпоинта, который попадает в OpenAPI.
- Сообщения в логах и тексты интерфейса — по-русски, имена сущностей и полей — как в данных кейса.

## Переменные окружения

Любая новая переменная приложения сразу появляется в `.env.example` с пустым или безопасным значением и в `app/core/config.py`. Реальные значения живут только в локальном `.env` и в секретах стенда — в репозиторий они не попадают никогда.
Переменные `WEB_PORT` и `POSTGRES_*` управляют только контейнерами и описаны в
[../deploy/README.md](../deploy/README.md). Ключ LLM и DSN хранятся в `Settings` как
`SecretStr`; для передачи клиенту используйте `.get_secret_value()`, не выводя результат в логи.
