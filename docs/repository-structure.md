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

Защита от инъекций A5-10 — `backend/app/llm/guard.py`, тесты с вредоносным материалом — `backend/tests/test_prompt_injection.py`.

Поведение при нехватке данных A5-11 — `backend/app/agent/fallback.py`: проверка цифр по источникам, пробелы `GeneratedInsight.gaps`, проверенные факты без модели.

Предложение продукта банка A5-12 — `backend/app/agent/offers.py`: правила уместности, `GeneratedInsight.offer`, проверка продуктов в действиях агента.

Привязка утверждений к фрагментам A5-13 — `ground` и `claims_of` в `backend/app/agent/trace.py`, поля `Statement.basis`/`support`, `AgentTrace.claims`.

Инсайт под должность A5-14 — `backend/app/agent/roles.py`: акценты по правилам и проверенные формулировки модели в `GeneratedInsight.role_view`, общий разбор для всех ролей.

Событие у конкурента или контрагента A5-15 — `backend/app/agent/related.py`: связь как признак профиля, `GeneratedInsight.related` с выжимкой и «что это значит для нас».

Связи между новостями A5-16 — `backend/app/agent/threads.py`, API — `backend/app/api/news_links.py` (`/api/news/threads`, `/api/news/{id}/links`).

Сводка за период A5-17 — `backend/app/agent/digest.py`, API — `backend/app/api/digest.py` (`POST /api/companies/{id}/digest`).

Снимки оценок ленты и проверка неизменности контекста — `backend/app/rank/snapshots.py`.
`frontend/src/App.tsx` подключает ленты, выбор роли, генерацию и чтение трассы.
