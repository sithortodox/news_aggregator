# news-aggregator

Персональный агрегатор новостей из Telegram: читает выбранные каналы и
группы, отсеивает рекламу и слишком короткие посты, находит повторяющиеся
новости (по тексту и по ссылкам) и публикует только уникальные посты — в
целевой Telegram-канал или в консоль.

## Возможности

- Чтение из Telegram-каналов/групп (через [Telethon](https://docs.telethon.dev/))
  или из фейкового источника — для локальной проверки без Telegram.
- Нормализация текста и sha256-хэширование для сравнения новостей.
- Дедупликация по тексту и по ссылкам первоисточника.
- Фильтрация коротких/пустых постов и рекламы по ключевым словам.
- Публикация в Telegram-канал или в консоль (легко добавить свой издатель).
- Асинхронное SQLite-хранилище состояния (курсоры источников, история хэшей/ссылок).
- Режим `dry-run` — прогон пайплайна без реальной публикации, только логи.
- Все компоненты подключаются через `ComponentRegistry` по имени из
  `config.yaml` — добавление нового фильтра/дедупликатора/издателя **не
  требует правки пайплайна**.
- Падение одного источника не останавливает обработку остальных.
- Опциональный Telegram-бот для управления списком каналов командами
  (`/add`, `/remove`, `/list`, `/pause`, `/resume`) — без правки
  `config.yaml` и без перезапуска процесса.

## Архитектура

```
src/news_aggregator/
├── core/            # Домен и оркестрация. НЕ зависит от Telegram/SQLite.
│   ├── models.py        Source, RawMessage, ProcessedMessage,
│   │                     DeduplicationResult, PipelineStats
│   ├── interfaces.py     ISourceReader, ISourceRepository, IFilter,
│   │                     IDeduplicator, IEnricher, IPublisher, IStorage (ABC)
│   ├── text_utils.py     normalize_text, extract_links, compute_text_hash
│   ├── registry.py       ComponentRegistry / TypedRegistry
│   └── pipeline.py       AggregatorPipeline — читает → нормализует →
│                          фильтрует → дедуплицирует → публикует → сохраняет
├── config/          # YAML + .env, без секретов в датаклассах
├── storage/         # SqliteStorage(IStorage), SqliteSourceRepository(ISourceRepository)
├── sources/          fake_source.py, telegram_source.py, telegram_client.py
├── filters/           length_filter.py, ad_filter.py
├── dedup/              hash_deduplicator.py, link_deduplicator.py
├── publishers/         console_publisher.py, telegram_publisher.py
├── bot/              commands.py (чистая логика), telegram_bot.py (обвязка PTB)
├── bootstrap.py     # Регистрация всех компонентов в ComponentRegistry
└── main.py          # CLI и точка входа
```

Ключевой принцип: **ядро (`core/`) не знает про Telegram, SQLite или любой
другой конкретный сервис** — оно работает только через интерфейсы
(`ISourceReader`, `ISourceRepository`, `IStorage` и т.д.). Конкретные
реализации регистрируются в `bootstrap.py` и подключаются по имени из
`config.yaml`.

Помимо `src/`, в корне репозитория:

```
config/config.yaml            рабочий конфиг (см. "Конфигурация" ниже)
tests/                         юнит- и интеграционные тесты
.github/workflows/ci.yml      CI: lint + типы + тесты + сборка образа
Dockerfile, docker-compose.yml, .dockerignore    контейнеризация
deploy/news-aggregator.service   systemd-юнит для деплоя без Docker
Makefile                       короткие алиасы для частых команд (make help)
.env.example                   шаблон секретов (см. "Установка")
```

## Установка

Требуется Python 3.11+.

```bash
git clone <этот репозиторий>
cd news_aggregator
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
make install                    # или: pip install -e ".[dev]"
cp .env.example .env
```

В репозитории есть `Makefile` с короткими алиасами для частых команд —
`make help` покажет список. Дальше в README используются и прямые команды,
и их `make`-эквиваленты — выбирайте, что удобнее.

## Быстрый старт: MVP без Telegram (dry-run)

Из коробки `config/config.yaml` настроен на фейковый источник — реальный
Telegram не требуется:

```bash
python -m news_aggregator.main --config config/config.yaml --once --verbose
# или: make run-once
```

Вы увидите в логах шесть демонстрационных сообщений: одно распознается как
дубль по ссылке, одно как реклама, одно как слишком короткое, три пройдут
как уникальные (в dry-run — только в логе с пометкой `[dry-run]`, без
реальной отправки).

Чтобы действительно напечатать уникальные посты в консоль:

```bash
python -m news_aggregator.main --config config/config.yaml --once --no-dry-run
```

## Подключение реального Telegram

1. Получите `api_id` и `api_hash` на <https://my.telegram.org/apps>.
2. Заполните `.env` (не коммитится, см. `.gitignore`):

   ```dotenv
   TELEGRAM_API_ID=123456
   TELEGRAM_API_HASH=your_api_hash
   TELEGRAM_SESSION_NAME=news_aggregator
   TELEGRAM_TARGET_CHANNEL=@your_target_channel
   ```

3. Добавьте каналы в `config/config.yaml` и, при необходимости, издатель.

### Добавление каналов

Самый простой способ — просто перечислить username каналов строками:

```yaml
sources:
  - "@meduzalive"
  - "@rian_ru"
  - "@another_channel"

publishers:
  - name: telegram_publisher
```

Для каждой строки `id`, `kind` (по умолчанию `telegram_channel`) и
`display_name` выводятся автоматически из username. Чтобы добавить новый
канал, достаточно дописать одну строку в список — никаких дополнительных
полей не требуется.

Если нужно что-то нестандартное — группа вместо канала, временно выключить
источник не удаляя его, свой `id` или отображаемое имя — можно указать
словарём; любые поля, кроме `identifier`, по-прежнему необязательны:

```yaml
sources:
  - "@meduzalive"                     # короткая форма
  - identifier: "@some_group"          # группа вместо канала
    kind: telegram_group
  - identifier: "@paused_channel"      # временно отключён
    enabled: false
  - identifier: "@custom_id_channel"
    id: my_custom_id                   # свой id вместо автогенерируемого
    display_name: "Мой любимый канал"
```

Обе формы можно свободно смешивать в одном списке `sources`. Если два
канала случайно дают одинаковый авто-`id`, второму автоматически
присваивается суффикс (`_2`, `_3`, ...). Если один и тот же канал указан
дважды — при запуске будет явная ошибка конфигурации, а не тихий дубль.

С появлением бота управления каналами (см. следующий раздел)
`config.yaml` — это, по сути, **начальный список** ("seed"): при каждом
запуске всё, что в нём перечислено, добавляется в хранилище (SQLite), если
там ещё нет канала с таким же `identifier`. Каналы, добавленные позже
командой `/add`, а также `/remove` через бота, при этом не трогаются и не
"откатываются" перезапуском — если сам YAML не менялся.

4. Запустите — при первом подключении Telethon интерактивно запросит код
   подтверждения Telegram и сохранит файл сессии (`*.session`, не
   коммитится).

## Бот управления каналами

Опционально, вместо (или вместе с) правкой `config.yaml`, списком каналов
можно управлять прямо из Telegram — командами `/add`, `/remove`, `/list`,
`/pause`, `/resume`. Это **отдельный** Telegram-бот (Bot API, библиотека
`python-telegram-bot`) — он не имеет отношения к Telethon-аккаунту, который
как читал каналы, так и продолжает их читать. Бот занимается только
списком каналов; публикация новостей остаётся такой, как настроена в
`publishers` (в канал или в консоль) — бот её не меняет.

### Настройка

1. Создайте бота через [@BotFather](https://t.me/BotFather) командой
   `/newbot` — получите токен вида `123456:AAExampleToken`.
2. Узнайте свой числовой Telegram user id — напишите
   [@userinfobot](https://t.me/userinfobot) (это **не** username, а именно
   число).
3. Впишите оба значения в `.env`:

   ```dotenv
   TELEGRAM_BOT_TOKEN=123456:AAExampleToken
   TELEGRAM_BOT_OWNER_ID=123456789
   ```

4. Запустите приложение в обычном (не `--once`) режиме — бот поднимется
   автоматически вместе с пайплайном:

   ```bash
   python -m news_aggregator.main --config config/config.yaml
   ```

Если `TELEGRAM_BOT_TOKEN`/`TELEGRAM_BOT_OWNER_ID` не заданы — поведение
полностью такое же, как без бота, ничего не меняется. Если задан только
токен без owner id — в лог пишется понятная ошибка, и бот просто не
стартует (остальной пайплайн продолжает работать). При `--once` бот не
запускается — он не нужен для разового прогона.

### Команды

| Команда | Что делает |
|---|---|
| `/list` | Показать все каналы (включая приостановленные) |
| `/add <identifier> [kind]` | Добавить канал (`kind` — `telegram_channel` по умолчанию, или `telegram_group`) |
| `/remove <id>` | Удалить канал |
| `/pause <id>` | Временно выключить канал, не удаляя |
| `/resume <id>` | Снова включить канал |

`id` для `/remove`/`/pause`/`/resume` смотрите в выводе `/list` — он
выводится автоматически из `identifier` (как и при коротком синтаксисе в
`config.yaml`, см. выше).

Бот отвечает только владельцу (`TELEGRAM_BOT_OWNER_ID`) — остальным
пользователям, даже если они найдут бота, вежливо откажет.

Добавленный через `/add` канал подхватывается пайплайном на следующем же
проходе (`pipeline.poll_interval_seconds`) — перезапускать процесс не
нужно.

## Режим постоянной работы

Без `--once` процесс опрашивает источники в цикле с интервалом
`pipeline.poll_interval_seconds` (можно переопределить `--interval`). Если
настроен бот управления каналами (см. выше), он работает в этом же
процессе параллельно с опросом источников.

```bash
python -m news_aggregator.main --config config/config.yaml --interval 120
```

Остановка — `Ctrl+C` (корректно останавливает и бота, если он был запущен).

## Конфигурация

Полный пример — `config/config.yaml`. Основные секции:

| Секция           | Назначение                                                        |
|------------------|--------------------------------------------------------------------|
| `pipeline`       | `dry_run`, `poll_interval_seconds`, `max_messages_per_source`      |
| `sources`        | **начальный** список источников (см. "Бот управления каналами" — актуальный список живёт в SQLite и может отличаться от YAML) |
| `filters`        | список `{name, params}` — фильтры из `ComponentRegistry.filters`   |
| `deduplicators`  | список `{name, params}` — дедупликаторы (получают `storage` автоматически) |
| `enrichers`      | список `{name, params}` — обогатители (пока пусто в MVP)           |
| `publishers`     | список `{name, params}` — издатели                                 |
| `storage`        | `{name, params}` — хранилище состояния (по умолчанию `sqlite`)     |

**Секреты никогда не хранятся в `config.yaml`** — только в `.env`
(`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_TARGET_CHANNEL`).

## Расширение: добавление нового компонента

Пайплайн **не меняется** при добавлении нового компонента. Например, новый
фильтр:

1. Создайте класс, реализующий `IFilter`, в `src/news_aggregator/filters/`.
2. Зарегистрируйте его в `bootstrap.py`:
   ```python
   registry.filters.register("my_filter", MyFilter)
   ```
3. Добавьте в `config.yaml`:
   ```yaml
   filters:
     - name: my_filter
       params:
         some_param: 42
   ```

Аналогично для `IDeduplicator`, `IEnricher`, `IPublisher`, `ISourceReader`,
`IStorage`, `ISourceRepository`.

## Docker

Проект можно запускать в контейнере — `Dockerfile` собирает образ на
`python:3.11-slim`, `docker-compose.yml` настраивает volume-ы для
конфигурации и состояния. Бот управления каналами (если настроен в `.env`)
поднимается автоматически вместе с пайплайном в том же контейнере — он
работает через long polling (исходящие соединения), поэтому никаких
дополнительных портов пробрасывать не нужно.

### MVP без Telegram в Docker (dry-run на fake-источнике)

```bash
docker compose up -d
docker compose logs -f
```

`.env` для этого не обязателен, но `env_file: .env` в `docker-compose.yml`
требует, чтобы файл существовал — создайте его в любом случае:

```bash
cp .env.example .env
```

### Подключение реального Telegram в Docker

1. Заполните `.env`, как описано выше в разделе «Подключение реального
   Telegram», и добавьте (это важно именно для Docker, чтобы файл сессии
   не терялся при пересоздании контейнера):

   ```dotenv
   TELEGRAM_SESSION_NAME=data/news_aggregator
   ```

2. Добавьте каналы в `config/config.yaml` (см. раздел «Добавление
   каналов» выше) и издатель `telegram_publisher`.

3. Первый запуск должен быть интерактивным — Telethon запросит код
   подтверждения Telegram в терминале:

   ```bash
   docker compose run --rm news-aggregator --config config/config.yaml --once
   ```

   Файл сессии сохранится в `./data/news_aggregator.session` на хосте
   (через volume) и будет переиспользован при следующих запусках без
   повторного запроса кода.

4. После этого можно запускать в фоне как обычный сервис:

   ```bash
   docker compose up -d
   ```

### Полезные команды

```bash
docker compose build              # пересобрать образ после изменения кода   (make docker-build)
docker compose logs -f            # следить за логами                        (make docker-logs)
docker compose down                # остановить и удалить контейнер (volume-ы останутся)  (make docker-down)
docker compose run --rm news-aggregator --config config/config.yaml --once --verbose  # разовый запуск с подробными логами  (make docker-run-once)
```

`./config` монтируется как `:ro` (read-only) — можно редактировать
`config.yaml` на хосте (например, добавить канал) и просто перезапустить
контейнер (`docker compose restart`), без пересборки образа.

## Деплой на VPS

Агрегатор не поднимает никаких сетевых сервисов и не принимает входящих
подключений — он только читает Telegram (исходящие соединения) и, опционально,
публикует туда же. Поэтому для VPS **не нужно открывать никакие порты** и не
нужен обратный прокси. Требования минимальны: 1 vCPU и 512 МБ–1 ГБ RAM с
запасом достаточно (Ubuntu 22.04/24.04 или Debian 12 в примерах ниже).

Ниже два равноценных варианта — выберите один. Docker проще в обслуживании
(обновления, изоляция), systemd — без лишней прослойки, если Docker на VPS
нежелателен.

### Подготовка VPS (общая для обоих вариантов)

```bash
ssh root@your-server-ip

# создаём отдельного пользователя — не работаем от root
adduser deploy
usermod -aG sudo deploy
su - deploy

# базовый firewall: разрешаем только SSH, входящих портов для агрегатора не нужно
sudo apt update && sudo apt install -y ufw
sudo ufw allow OpenSSH
sudo ufw enable
```

Далее получите код на сервер — проще всего через git:

```bash
git clone <адрес вашего репозитория> news_aggregator
cd news_aggregator
cp .env.example .env
nano .env   # впишите TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION_NAME
nano config/config.yaml   # добавьте свои каналы (см. раздел "Добавление каналов")
```

Если репозитория с проектом ещё нет, можно просто скопировать файлы
локально собранным архивом: `scp news_aggregator.zip deploy@server:~/` и
`unzip news_aggregator.zip` на сервере.

### Вариант A: Docker (рекомендуется)

1. Установите Docker Engine и плагин Compose (официальный скрипт для
   Ubuntu/Debian):

   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER
   newgrp docker   # или перезайдите по SSH, чтобы группа docker применилась
   sudo systemctl enable --now docker   # чтобы Docker поднимался при перезагрузке VPS
   ```

2. В `.env` укажите путь сессии внутрь volume, чтобы она не терялась при
   пересоздании контейнера:

   ```dotenv
   TELEGRAM_SESSION_NAME=data/news_aggregator
   ```

3. Первый запуск — интерактивный, для ввода кода подтверждения Telegram:

   ```bash
   docker compose run --rm news-aggregator --config config/config.yaml --once
   ```

4. Запустите в фоне — `restart: unless-stopped` в `docker-compose.yml`
   сам поднимет контейнер после `docker restart`/сбоя, а `systemctl enable
   docker` на шаге 1 — после перезагрузки всей VPS:

   ```bash
   docker compose up -d
   docker compose logs -f   # проверить, что всё поднялось
   ```

Обновление кода после `git pull`:

```bash
git pull
docker compose up -d --build
```

### Вариант B: без Docker, через systemd

1. Установите Python 3.11+ и создайте отдельного системного пользователя:

   ```bash
   sudo apt update && sudo apt install -y python3.11 python3.11-venv
   sudo useradd --system --create-home --shell /usr/sbin/nologin news-aggregator
   sudo mkdir -p /opt/news_aggregator
   sudo chown news-aggregator:news-aggregator /opt/news_aggregator
   ```

2. Разместите код в `/opt/news_aggregator` (git clone или распакованный
   архив) и установите зависимости от имени этого пользователя. Обратите
   внимание: `deploy/news-aggregator.service` использует `ProtectSystem=strict`
   с доступом на запись только в `data/`, поэтому файл сессии Telethon
   обязательно должен лежать внутри неё — не забудьте
   `TELEGRAM_SESSION_NAME=data/news_aggregator` в `.env` (как и в
   Docker-варианте), иначе сервис не сможет обновлять сессию после
   первого запуска:

   ```bash
   sudo -u news-aggregator -H bash -c '
     cd /opt/news_aggregator &&
     python3.11 -m venv .venv &&
     .venv/bin/pip install . &&
     cp .env.example .env
   '
   sudo -u news-aggregator nano /opt/news_aggregator/.env
   #   TELEGRAM_SESSION_NAME=data/news_aggregator   <- обязательно для systemd-варианта
   sudo -u news-aggregator nano /opt/news_aggregator/config/config.yaml
   ```

3. Выполните первый интерактивный вход Telethon **до** запуска сервиса
   (systemd не сможет ввести код подтверждения за вас):

   ```bash
   sudo -u news-aggregator -H bash -c '
     cd /opt/news_aggregator &&
     .venv/bin/python -m news_aggregator.main --config config/config.yaml --once
   '
   ```

4. Установите и включите systemd-юнит (шаблон уже в репозитории —
   `deploy/news-aggregator.service`):

   ```bash
   sudo cp deploy/news-aggregator.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now news-aggregator
   sudo systemctl status news-aggregator
   ```

Логи: `journalctl -u news-aggregator -f`.

Обновление кода после `git pull`:

```bash
sudo -u news-aggregator -H bash -c 'cd /opt/news_aggregator && git pull && .venv/bin/pip install .'
sudo systemctl restart news-aggregator
```

### Резервное копирование состояния

Всё состояние (курсоры источников, история хэшей/ссылок, файл сессии
Telethon) лежит в одном месте: `data/` (Docker-вариант) или
`/opt/news_aggregator/data` + `.session`-файл (systemd-вариант). Достаточно
периодически копировать эту директорию, например через `cron` + `rsync`
на другой хост — восстановление после сбоя VPS сводится к развёртыванию
проекта заново и подстановке сохранённой `data/`.

## Валидация конфигурации перед деплоем

Перед тем как перезапускать сервис на VPS после правки `config.yaml` или
`.env`, стоит проверить их без риска уронить работающий процесс:

```bash
python -m news_aggregator.main --config config/config.yaml --validate-config
# или: make validate-config
```

Команда собирает весь пайплайн (включая Telegram-компоненты — секреты и
клиент, но без выхода в сеть) и печатает сводку по источникам, фильтрам,
дедупликаторам и издателям, либо явно сообщает об ошибке (например, о
дублирующемся канале или отсутствующем `TELEGRAM_API_ID`) и завершается с
кодом 1. Пайплайн при этом не запускается и курсор источников не трогается.

Пример из инструкций по обновлению кода на VPS (Docker):

```bash
git pull
docker compose run --rm news-aggregator --config config/config.yaml --validate-config \
  && docker compose up -d --build
```

## Тесты

```bash
pytest
# или: make test
# make check — lint (ruff) + типы (mypy) + тесты, то же самое, что гоняет CI
```

При каждом push/PR в GitHub тот же набор проверок (`ruff check`,
`ruff format --check`, `mypy`, `pytest` на Python 3.11 и 3.12, плюс сборка
Docker-образа) автоматически запускается в GitHub Actions —
`.github/workflows/ci.yml`.

Юнит-тесты не используют реальный Telegram — Telegram-адаптеры тестируются
через лёгкие тестовые двойники клиента. Дедупликаторы и пайплайн
тестируются через `InMemoryStorage`/`InMemorySourceRepository` (см.
`tests/support/`), а `SqliteStorage`/`SqliteSourceRepository` — отдельными
тестами с временной БД. Бизнес-логика команд бота (`bot/commands.py`)
тестируется без установленного `python-telegram-bot` — она не зависит от
этой библиотеки напрямую; сама обвязка `bot/telegram_bot.py` — тонкий слой
поверх неё.

## Известные ограничения

- `AdFilter` сравнивает точные подстроки нормализованного текста и не
  учитывает словоизменение (например, ключевое слово "скидка" не совпадёт
  со словоформой "скидкой"). Для лучшего покрытия перечисляйте нужные
  словоформы явно в `config.yaml`.
- Дедупликация не использует семантическое сходство (embeddings) — только
  точное совпадение нормализованного текста или ссылки. Разные
  переформулировки одной новости без общей ссылки не будут признаны дублями.
- Если канал одновременно объявлен в `config.yaml` и удалён через `/remove`
  в боте, при следующем перезапуске он снова появится (YAML — это
  "начальный список", который досеивается при каждом старте, если канала
  с таким `identifier` ещё нет в БД). Чтобы удалить канал насовсем, уберите
  его и из `config.yaml`.
- Бот управления каналами рассчитан на одного владельца
  (`TELEGRAM_BOT_OWNER_ID`) — это персональный инструмент, а не
  многопользовательский сервис; всем остальным пользователям бот отвечает
  отказом.

## Запрещено (и не реализовано намеренно)

- Хранение секретов в YAML или коде.
- Коммит `.env`, `*.session`, файлов БД.
- Смешивание Telegram API с бизнес-логикой (вынесено в `sources/`/`publishers/`).
- Удаление или редактирование чужих сообщений в каналах.
- Реальный Telegram в юнит-тестах.
