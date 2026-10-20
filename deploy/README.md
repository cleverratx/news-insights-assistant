# Запуск через Docker Compose

Нужны Docker Engine и Docker Compose v2. Из корня репозитория:

```bash
cp .env.example .env
docker compose up -d --build --wait
```

Без ключа LLM доступны инфраструктура и каркас приложения. Анализ новостей появится
в следующих задачах. Перед публикацией замените учебный пароль `change-me`
одновременно в `POSTGRES_PASSWORD` и `DATABASE_URL`.

| Сервис | Назначение | Проверка готовности |
| --- | --- | --- |
| `postgres` | PostgreSQL 17, том `postgres-data` | `pg_isready` |
| `redis` | Redis 7.4, очередь и кеш, AOF в `redis-data` | `redis-cli ping` |
| `api` | FastAPI, Python 3.12, зависимости из `uv.lock` | HTTP `/health` |
| `worker` | arq, тот же Dockerfile и настройки, что у API | `arq --check`, heartbeat в Redis |
| `web` | React, сборка Node 22 из `package-lock.json`, nginx | HTTP `/health` и проксирование `/api/health` |

API и worker ожидают готовности PostgreSQL и Redis; nginx ожидает API.
Worker пока выполняет только проверочную задачу `ping`; парсеры и расписания добавляются
отдельно. `LOG_LEVEL` управляет Uvicorn; arq использует стандартный логгер.

- Интерфейс: `http://localhost:8080`.
- API через nginx: `http://localhost:8080/api/health`.
- Swagger: `http://localhost:8080/api/docs`.
- Прямой доступ к API: `http://localhost:8000/health`.

Порты привязаны к `127.0.0.1`; для внешнего стенда настраивается входящий прокси.
PostgreSQL и Redis доступны только в сети Compose. `APP_PORT` меняет порт API
и адрес проксирования nginx; `WEB_PORT` меняет порт интерфейса.
Каталог `data/` подключён к API и worker как `/app/data` только для чтения; в образ не входит.

## Управление

```bash
docker compose ps
docker compose logs --tail=100 api worker
docker compose down
```

`down` сохраняет тома. Пароль PostgreSQL применяется при первой инициализации тома:
изменение `.env` не меняет пароль уже созданного пользователя.

## Переменные окружения

Шаблон — [../.env.example](../.env.example). Compose подставляет значения из `.env`
и окружения хоста в окружение API и worker при запуске; окружение хоста имеет приоритет.
nginx получает только адрес API; браузер использует относительный `/api`.
Сборка фронтенда не получает `.env` и ключ LLM.

| Переменные | Назначение |
| --- | --- |
| `APP_ENV` | `local`, `ci` или `stand` |
| `APP_PORT`, `LOG_LEVEL` | порт API и уровень логов Uvicorn |
| `WEB_PORT` | порт nginx на хосте, только для Compose |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | инициализация PostgreSQL, только для Compose |
| `DATABASE_URL` | SQLAlchemy DSN; в Compose хост `postgres` |
| `REDIS_URL` | DSN Redis; в Compose хост `redis` |
| `LLM_BASE_URL`, `LLM_API_KEY` | адрес провайдера и ключ; до A1-07 могут быть пустыми |
| `LLM_MODEL_FAST`, `LLM_MODEL_SMART` | идентификаторы разрешённых моделей у провайдера |
| `LLM_DAILY_BUDGET_RUB` | положительный дневной бюджет; учёт расхода реализуется в A1-07 |
| `LLM_REQUEST_TIMEOUT_SECONDS` | положительный таймаут вызова LLM |
| `NEWS_SOURCES_FILE` | путь к будущей конфигурации источников |
| `NEWS_FETCH_INTERVAL_MINUTES`, `NEWS_USER_AGENT` | период сбора и User-Agent будущего парсера |
| `DATA_DIR` | каталог данных; `/app/data` в Compose |

Секреты хранятся в `.env` или окружении стенда. Варианты `.env` исключены из Git,
а контекст Docker содержит только разрешённые исходники и lock-файлы.
`LLM_API_KEY`, `DATABASE_URL`, `REDIS_URL` в настройках имеют тип `SecretStr`:
обычный вывод модели и её JSON маскируют значения. Интеграции получают секреты явно
через `.get_secret_value()`; такие значения нельзя писать в логи.
Вывод `docker compose config` содержит окружение; проверяйте его без вывода значений:

```bash
docker compose config --quiet
```
