# Борт — персональная система управления проектами

Локальная сводка по всем проектам: деньги, дедлайны, задачи, чаты. Одна SQLite-база,
два потребителя: человек — веб-интерфейс на `http://localhost:8100`, AI-ассистент — MCP-сервер.
Числа в UI и у ассистента всегда совпадают: оба идут через один сервисный слой.

## Старт

```bash
cd ~/bort
uv sync                                  # зависимости, venv на Python 3.13
uv run python scripts/migrate.py         # создать/обновить схему БД (идемпотентно)
uv run uvicorn bort.web.app:app --host 127.0.0.1 --port 8100   # вручную
```

Открыть `http://localhost:8100`. API-документация (Swagger): `http://localhost:8100/docs`.
Сервер слушает только `127.0.0.1` — доступ снаружи только через SSH-туннель.

### Запуск через PM2 (рекомендуется)

```bash
cd ~/bort
pm2 start ecosystem.config.js     # процесс bort-web, логи в ~/bort/logs/
pm2 save                          # запомнить список процессов
pm2 startup                       # автозапуск при загрузке Mac mini (один раз, выполнить вывод команды)
pm2 restart bort-web              # перезапуск
pm2 logs bort-web                 # смотреть логи
```

Если PM2 не установлен: `npm i -g pm2`. Без него сервис стартует вручную командой
`uv run uvicorn bort.web.app:app --host 127.0.0.1 --port 8100` из `~/bort`.

## Подключение MCP к Hermes

Добавить секцию `mcp_servers` в конфиг Hermes (`~/.hermes/config.yaml`; если секция уже есть —
дописать ключ `bort` внутрь):

```yaml
mcp_servers:
  bort:
    command: <ПУТЬ К РЕПО>/.venv/bin/python
    args: ["-m", "bort.mcp.server"]
    env:
      BORT_DB: <ПУТЬ К РЕПО>/data/bort.db
      BORT_TZ: Europe/Moscow
```

Транспорт — stdio: Hermes сам запускает процесс, открытых портов нет.
Проверка вручную: `echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' | BORT_DB=~/bort/data/bort.db ~/bort/.venv/bin/python -m bort.mcp.server`
— в stdout придёт JSON-ответ с `serverInfo.name == "bort"`.
Ресурс `bort://summary` — markdown-сводка одним чтением.

## Инструменты MCP

| Инструмент | Что делает |
|---|---|
| `bort_project_list` | Список проектов (краткая форма), фильтры статус/приоритет/поиск |
| `bort_project_get` | Проект целиком: задачи, затраты, чаты, люди (по id или названию) |
| `bort_project_create` | Создать проект; сумма в рублях или копейках |
| `bort_project_update` | Обновить поля проекта (по id или названию) |
| `bort_task_create` | Создать задачу в проекте |
| `bort_task_update` | Обновить задачу (статус, приоритет, дедлайн…) |
| `bort_task_close` | Закрыть задачу: `done` или `cancelled`, проставляет `closed_at` |
| `bort_expense_add` | Добавить затрату; в ответе — новая маржа проекта |
| `bort_summary` | Сводка: агрегаты по деньгам + счётчики «просрочено»/«горит» |
| `bort_project_summary` | Сводка одного проекта: маржа, прогресс задач, разбивка по категориям |
| `bort_chat_attach` | Привязать чат к проекту (существующий или создать новый) |
| `bort_chat_detach` | Отвязать чат от проекта (чат остаётся в справочнике) |
| `bort_chat_list` | Чаты с участниками и привязками к проектам |
| `bort_person_upsert` | Создать/обновить человека, сразу привязать к проекту/чату с ролью |

Все инструменты возвращают `{"ok": true, ...}` либо `{"ok": false, "error": {code, message, details}}`.
Проект ищется по названию регистронезависимо; неоднозначность → `conflict` со списком кандидатов.

## REST API

База: `http://127.0.0.1:8100/api/v1`. Полный список — в Swagger (`/docs`). Основное:

```
GET    /api/v1/summary?scope=open|active|all&q=   сводка с агрегатами
GET    /api/v1/projects                           список (status, priority, q, limit, offset, sort)
POST   /api/v1/projects                           создать (name*, deal_amount или deal_amount_minor)
GET    /api/v1/projects/{id}                      проект + задачи, затраты, чаты, люди
PATCH  /api/v1/projects/{id}                      частичное обновление
DELETE /api/v1/projects/{id}                      удалить (каскадом задачи и затраты)
POST   /api/v1/projects/{id}/tasks                задача ·  POST /tasks/{id}/close — закрыть
POST   /api/v1/projects/{id}/expenses             затрата ·  PATCH/DELETE /expenses/{id}
GET    /api/v1/expense-categories                 категории ·  POST — добавить
GET/POST /api/v1/chats, /api/v1/people            справочники
POST   /api/v1/projects/{id}/chats                привязать чат ·  DELETE .../chats/{chat_id}
POST   /api/v1/projects/{id}/people               привязать человека ·  DELETE .../people/{person_id}
GET    /api/v1/health                             {status, db_path, schema_version, wal}
GET    /api/v1/meta/enums                         статусы/приоритеты с русскими подписями
```

Деньги: канон — целые копейки (`*_minor`); человекочитаемая строка («150 000,50»)
принимается в полях `deal_amount`/`amount` и отдаётся рядом с `*_minor`.

## Бэкапы

```bash
~/bort/scripts/backup.sh
```

Копия через `sqlite3 .backup` (корректно при WAL) → `~/bort/backups/bort-YYYYMMDD-HHMMSS.db`,
хранятся последние 14 копий. Переменные: `BORT_DB`, `BORT_BACKUP_DIR`. Код возврата — 0/не-0.
Тот же скрипт вызывает кнопка «Сделать бэкап» на странице `/settings`.

Ежедневный бэкап в 21:00 — добавить в crontab (`crontab -e`, НЕ добавлено автоматически):

```
0 21 * * * ~/bort/scripts/backup.sh >> ~/bort/logs/backup.log 2>&1
```

## Демо-данные

```bash
uv run python scripts/seed_demo.py          # 6 демо-проектов (если база пуста)
uv run python scripts/seed_demo.py --wipe   # убрать ВСЁ: проекты (с задачами и затратами), чаты, люди
```

## Тесты и структура

```bash
uv run pytest tests/ -q
```

Схема БД: `migrations/001_init.sql` (деньги — INTEGER копейки; даты — `YYYY-MM-DD` без TZ;
WAL; foreign_keys=ON на каждом соединении). Логика — в `src/bort/services/`, REST и MCP —
тонкие транспорты над ней, поэтому числа в UI и у ассистента не расходятся.
