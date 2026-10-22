# Структура репозитория

```
.
├── .github/
│   ├── ISSUE_TEMPLATE/         шаблоны задачи и бага
│   ├── rulesets/               правила защиты main и именования (импортируются в GitHub)
│   ├── workflows/              CI: проверки backend и frontend
│   └── pull_request_template.md
├── backend/
│   ├── app/
│   │   ├── agent/              оркестратор: план, инструменты, контекст, самопроверка
│   │   ├── api/                HTTP-контракт и схемы ответов
│   │   ├── contracts/          схемы обмена между модулями (источник docs/schemas)
│   │   ├── core/               конфигурация, логи, клиент LLM
│   │   ├── data/               загрузка выгрузок кейса в PostgreSQL
│   │   ├── news/               сбор и нормализация новостей
│   │   ├── profile/            профиль компании по выгрузкам кейса
│   │   ├── rank/               отбор кандидатов и ранжирование
│   │   ├── worker/             фоновые задачи
│   │   └── main.py             точка входа FastAPI
│   ├── scripts/                разбор выгрузок и отбор демо-компаний
│   ├── tests/
│   ├── pyproject.toml          зависимости и настройки инструментов
│   ├── uv.lock                 зафиксированные версии Python-зависимостей
│   └── .python-version         версия Python
├── frontend/
│   ├── src/
│   │   ├── pages/              экраны
│   │   └── shared/             переиспользуемое: клиент API, UI, хуки
│   ├── package.json
│   ├── package-lock.json       зафиксированные версии Node-зависимостей
│   └── .nvmrc                  версия Node
├── data/
│   ├── samples/                синтетические строки для тестов и демо
│   └── README.md
├── deploy/                     Dockerfile, compose, конфиги стенда
├── docs/                       документация проекта
│   ├── decisions/              ADR
│   └── schemas/                JSON-схемы контрактов и примеры
├── eval/                       эталонная разметка, замеры качества отбора и прогон на новых новостях
├── scripts/                    вспомогательные скрипты
├── .editorconfig
├── .env.example                все переменные окружения с пустыми значениями
├── .gitattributes
├── .gitignore
├── README.md
└── THIRD_PARTY_NOTICES.md      сторонние компоненты и лицензии
```

Правила, которые держим:

- Модуль бэкенда = зона ответственности в команде = область в имени ветки и коммита.
- Всё, что нужно для запуска, лежит в `deploy`; в коде путей до конкретной машины нет.
- Данные и секреты в репозиторий не попадают: в `data/samples` только синтетика, в `.env.example` только имена переменных.

Оркестратор агента A5-01: `backend/app/agent/orchestrator.py`; цикл, лимиты и подключение инструментов описаны в [agent.md](agent.md).

Инструменты агента A5-03: `backend/app/agent/tools/`, фабрика реестра — `tools/builtin.py`, расчёт платёжной доли — `app/profile/exposure.py`.

Контекст агента и память наблюдений A5-04 — `backend/app/agent/context.py`; повторное использование результатов выполняет оркестратор.

Критик A5-05 — `backend/app/agent/critic.py`, контракт отчёта — `backend/app/contracts/critic.py`.

Генерация A5-06/A5-07 — `backend/app/agent/insight.py`, API — `backend/app/api/insights.py`.
Журнал и хранение разборов A5-08 — `backend/app/agent/trace.py`, контракт —
`backend/app/contracts/trace.py`; результат и трасса хранятся в таблице `agent_insights`.

Управление стоимостью A5-09: маршруты, квоты и экономный режим — `backend/app/llm/client.py`, суточные счётчики — `backend/app/llm/budget.py` и `usage.py`, расход на запрос — `backend/app/llm/spend.py`, кеш инсайтов — `backend/app/agent/insight_cache.py`.

Снимки оценок ленты и проверка неизменности контекста — `backend/app/rank/snapshots.py`.
`frontend/src/App.tsx` подключает ленты, выбор роли, генерацию и чтение трассы.
