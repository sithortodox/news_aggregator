# news-aggregator

Персональный агрегатор новостей из Telegram: читает выбранные каналы и
группы, отсеивает рекламу и слишком короткие посты, находит повторяющиеся
новости (по тексту и по ссылкам) и публикует только уникальные посты — в
целевой Telegram-канал или в консоль.

## Возможности

- Чтение из Telegram-каналов/групп (через [Telethon](https://docs.telethon.dev/))
  или из фейкового источника — для локальной проверки без Telegram.
- Нормализация текста (агрессивная: хэштеги/упоминания, растянутые
  повторы символов) и sha256-хэширование для сравнения новостей.
- Трёхуровневая дедупликация: точное совпадение текста/ссылки, SimHash
  (почти дословные повторы) и сравнение по леммам через коэффициент
  Жаккара (ловит пересказы одной новости *разными словами* в разных
  каналах — то, чего SimHash принципиально не может, см. раздел
  "Дедупликация").
- Фильтрация коротких/пустых постов и рекламы по ключевым словам.
- Публикация в Telegram-канал или в консоль (легко добавить свой издатель).
  В Telegram-канал пересылаются и фото/документы (без скачивания —
  сервером Telegram), сохраняются гиперссылки и форматирование исходного
  поста, и в конец добавляется подпись с именем канала-источника.
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
│   ├── models.py        Source, RawMessage (+ MediaAttachment,
│   │                     formatting_entities), ProcessedMessage,
│   │                     DeduplicationResult, PipelineStats
│   ├── interfaces.py     ISourceReader, ISourceRepository, ISimhashIndex,
│   │                     ILemmaSetIndex, IFilter, IDeduplicator, IEnricher,
│   │                     IPublisher, IStorage (ABC)
│   ├── text_utils.py     normalize_text, extract_links, compute_text_hash
│   ├── simhash.py         compute_simhash (char/word шинглы), hamming_distance
│   ├── lemmatize.py       extract_lemma_set, jaccard_similarity (pymorphy3)
│   ├── registry.py       ComponentRegistry / TypedRegistry
│   └── pipeline.py       AggregatorPipeline — читает → нормализует →
│                          фильтрует → дедуплицирует → публикует → сохраняет
├── config/          # YAML + .env, без секретов в датаклассах
├── storage/         # SqliteStorage, SqliteSourceRepository,
│                      SqliteSimhashIndex, SqliteLemmaIndex
├── sources/          fake_source.py, telegram_source.py, telegram_client.py
├── filters/           length_filter.py, ad_filter.py
├── dedup/              hash_deduplicator.py, link_deduplicator.py,
│                        simhash_deduplicator.py, paraphrase_deduplicator.py
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

> `pip install -e ".[dev]"` ставит и словарь `pymorphy3-dicts-ru` (нужен
> `paraphrase_deduplicator`, см. раздел "Дедупликация") — он тяжелее
> остальных зависимостей, первая установка/сборка образа может занять
> заметно больше времени, чем ожидалось от списка пакетов.

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

## Публикация в Telegram: медиа, ссылки, подпись источника

`telegram_publisher` не просто пересылает голый текст — он старается
сохранить пост максимально близким к оригиналу:

- **Фото и документы** пересылаются через `send_file` с media-объектом
  исходного сообщения напрямую — Telegram копирует файл на своей стороне,
  без скачивания и повторной загрузки агрегатором. Превью ссылок
  (`MessageMediaWebPage`) при этом не считаются файлом и публикуются как
  обычный текст со ссылкой — это не вложение, которое можно переслать.
- **Гиперссылки и форматирование** (жирный текст, ссылка на слове/фразе, а
  не вставленная в текст как есть, и т.п.) сохраняются как есть — ридер
  забирает `entities` исходного сообщения Telegram и publisher передаёт их
  без изменений (`formatting_entities`), не пропуская через Markdown/HTML
  парсер. Без этого ссылка, оформленная как гиперссылка на слове
  ("Подробнее" со скрытым URL), терялась бы при публикации — в тексте
  сообщения такой ссылки нет, она есть только в entities.
- **Подпись источника** — в конец текста добавляется `\n\nИсточник:
  <имя канала>` (`display_name` источника, см. "Добавление каналов" выше).
  Добавляется строго после исходного текста, чтобы не сдвинуть смещения
  entities (они считаются в UTF-16 code units от начала сообщения).

Ничего из этого не настраивается через `config.yaml` — это поведение
`telegram_publisher` по умолчанию.

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
| `deduplicators`  | список `{name, params}` — дедупликаторы (см. раздел "Дедупликация" ниже) |
| `enrichers`      | список `{name, params}` — обогатители (пока пусто в MVP)           |
| `publishers`     | список `{name, params}` — издатели                                 |
| `storage`        | `{name, params}` — хранилище состояния (по умолчанию `sqlite`)     |

**Секреты никогда не хранятся в `config.yaml`** — только в `.env`
(`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_TARGET_CHANNEL`).

## Дедупликация

Дедупликаторы в `config.yaml` проверяются **по порядку** и пайплайн
останавливается на первом, который признал сообщение дублем — поэтому
дешёвые точные проверки идут первыми, SimHash — дороже и после них, а
самая дорогая эвристика (лемматизация) — последней:

```yaml
deduplicators:
  - name: text_hash_deduplicator      # точное совпадение нормализованного текста
  - name: link_deduplicator            # общая ссылка на первоисточник
  - name: simhash_deduplicator          # почти дословное совпадение (см. ниже)
    params:
      max_hamming_distance: 4
      lookback_hours: 12
      shingle_size: 3
  - name: paraphrase_deduplicator       # пересказ той же новости другими словами
    params:
      min_jaccard: 0.17
      lookback_hours: 24
      min_lemmas: 4
```

### Агрессивная нормализация (уровень 1)

Прежде чем текст вообще попадает в хэш, SimHash или лемматизацию,
`normalize_text` (см. `core/text_utils.py`) агрессивно его упрощает: Unicode
NFKC, удаление хэштегов/упоминаний (`#tag`, `@channel`) целиком, удаление
ссылок, нижний регистр, схлопывание растянутых повторов символов
(`"оооочень"` -> `"оочень"`, но `"касса"` не трогается — порог 3+ повторов),
удаление пунктуации/эмодзи, схлопывание пробелов. Это уже само по себе
ловит хорошую долю "дублей" за счёт `text_hash_deduplicator`, почти
бесплатно.

### SimHash (уровень 2): почти дословные повторы

`simhash_deduplicator` считает [SimHash](https://en.wikipedia.org/wiki/SimHash)
по n-граммам нормализованного текста и сравнивает расстояние Хэмминга с
сообщениями за последние `lookback_hours` часов (не дольше — иначе индекс
отпечатков рос бы бесконечно; после каждого сохранения устаревшие записи
вне окна автоматически удаляются). Есть два режима шинглов (`unit` в
параметрах, по умолчанию `char`):

- `char` — символьные n-граммы. Ловит почти дословные повторы: тот же
  текст с другой пунктуацией, приставкой источника в конце, случайным
  эмодзи, который не убрала нормализация.
- `word` — пословные n-граммы. Устойчивее к незначительным перестановкам
  слов, но экспериментально не даёт значимо лучшего результата на
  переформулировках, чем `char` (см. следующий раздел про то, почему для
  этого нужна лемматизация, а не другой вид шинглов).

**Важно понимать масштаб задачи**: сам по себе SimHash — не семантическая
дедупликация, независимо от `unit`. Он ловит только **почти дословные**
повторы. Два независимых пересказа одного события *разными словами*
(например, "ЦБ поднял ставку" vs "Центробанк повысил ставку до рекордного
уровня") ни в char-, ни в word-режиме SimHash, скорее всего, не поймает —
общих n-грамм (символьных или пословных) между такими текстами почти нет.
Для этого случая нужен уровень 3 (см. ниже).

На реальных примерах (посты про одно и то же событие, различающиеся
только декором/приставкой) расстояние Хэмминга обычно получается 0–7, а
между текстами о разных событиях — 25–40+. Порог по умолчанию (4) выбран
консервативно, с большим запасом от этого "шумового пола"; если некоторые
почти-дубли всё же проходят как уникальные, можно смело поднять
`max_hamming_distance` до 8–10 — запас прочности большой. Компромисс
обратный: чем выше порог, тем выше риск случайно принять две разные, но
похожие по структуре новости за дубль.

### Лемматизация + Жаккар (уровень 3): пересказы разными словами

`paraphrase_deduplicator` решает именно то, что SimHash не может: одна и
та же новость, пересказанная разными каналами разными словами. Проблема
"в лоб" (сравнивать по словам или n-граммам без изменений) — русская
словоформа: падежи и спряжения меняют само слово (`"ставку"` vs
`"ставки"`, `"открылась"` vs `"открытии"`), поэтому даже пословные шинглы
почти не пересекаются у двух пересказов одной новости.

Решение — привести каждое слово к словарной форме (лемме) через
[pymorphy3](https://github.com/no-plagiarism/pymorphy3) (офлайн, без
сети/ML-весов — используется только для русского языка), отбросить
стоп-слова, и сравнить получившиеся множества лемм через коэффициент
Жаккара (`|A∩B| / |A∪B|`). На проверочных примерах (пересказы одной
новости из разных источников) это даёт 0.15–0.22, а между текстами о
разных событиях — ровно 0.0. Порог по умолчанию (`min_jaccard: 0.17`)
выбран консервативно между этими двумя диапазонами.

Короткие тексты (после лемматизации и удаления стоп-слов остаётся меньше
`min_lemmas` токенов) в сравнении не участвуют вовсе — на малом числе
токенов коэффициент Жаккара становится случайным и даёт ложные
срабатывания.

Это по-прежнему не полноценная семантическая дедупликация — два текста об
одном и том же, но без единого общего значимого слова, всё ещё не
поймать, — но существенно лучше, чем ничего, для типичного случая
пересказа новости похожими терминами.

### Несколько независимых индексов SimHash/лемм в одной базе

И `simhash_deduplicator`, и `paraphrase_deduplicator` хранят свои отпечатки
в отдельной таблице SQLite (`seen_simhashes` / `seen_lemma_sets` по
умолчанию). Если нужно несколько независимых конфигураций одного и того
же дедупликатора одновременно (например, `char`- и `word`-режим SimHash
параллельно) — задайте разным записям в `config.yaml` разный `index_name`,
и они не будут смешивать несравнимые отпечатки в одной таблице:

```yaml
  - name: simhash_deduplicator
    params:
      unit: char
      index_name: seen_simhashes        # можно не указывать - это значение по умолчанию
  - name: simhash_deduplicator
    params:
      unit: word
      max_hamming_distance: 10
      index_name: seen_simhashes_word    # обязательно другое имя, иначе отпечатки смешаются
```

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
`IStorage`, `ISourceRepository`, `ISimhashIndex`, `ILemmaSetIndex`.

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

### Резервное копирование и обслуживание БД

Состояние (курсоры, история дедупликации, список каналов) лежит в одной
SQLite-базе `data/state.db`. Для неё есть модуль `news_aggregator.maintenance`:

```bash
# консистентная сжатая копия «на лету» (контейнер останавливать не нужно)
docker compose exec -T news-aggregator python -m news_aggregator.maintenance backup
# вернуть место на диске после массового удаления записей (обычно не нужно)
docker compose exec -T news-aggregator python -m news_aggregator.maintenance vacuum
```

Копии кладутся в `data/backups/state-<UTC-время>.db`. Копия проверяется
через `PRAGMA integrity_check` и переименовывается в итоговое имя только
после успеха — недописанный бэкап не выглядит готовым. Без Docker — то же
самое через `python -m news_aggregator.maintenance ...`.

**Ротация.** После каждого успешного бэкапа удаляются копии старше
`--max-age-hours` (по умолчанию **24 часа**: окна дедупликации — 12 ч и
меньше, так что более старые копии не нужны) и копии сверх лимита `--keep`
(по умолчанию 7). Возраст считается по метке времени в имени файла, а не
по `mtime`. **Самая свежая копия не удаляется никогда** — даже если она
старше лимита, поэтому при сломавшемся cron вы не останетесь вообще без
бэкапа. Файлы с другими именами в `data/backups` не трогаются.

Так как хранится ~сутки, делайте бэкап **чаще раза в сутки** — например,
каждые 6 часов: тогда всегда есть 4–5 точек восстановления, а потеря при
сбое — не более 6 часов данных. При 13 МБ на копию это ~65 МБ.

Бэкап на том же диске защищает от порчи БД, но **не от потери VPS**.
Скрипт `deploy/backup.sh` делает бэкап и, если задан `BACKUP_REMOTE`,
копирует его за пределы сервера через [rclone](https://rclone.org)
(Google Drive, S3, SFTP и др.), удаляя там копии старше `BACKUP_REMOTE_MAX_AGE`
(по умолчанию 24 часа, формат rclone: `24h`, `7d`). Переменные скрипта:
`BACKUP_MAX_AGE_HOURS`, `BACKUP_KEEP`, `BACKUP_REMOTE`, `BACKUP_REMOTE_MAX_AGE`.

```bash
crontab -e
# каждые 6 часов; вывод — в syslog, а не в растущий файл
17 */6 * * * BACKUP_REMOTE=gdrive:news-aggregator-backups /home/deploy/news_aggregator/deploy/backup.sh 2>&1 | logger -t news-aggregator-backup
```

Без вывоза за пределы сервера уберите `BACKUP_REMOTE=...` из строки.

**Восстановление из копии:**

```bash
docker compose stop
cp data/backups/state-YYYYMMDD-HHMMSS.db data/state.db
rm -f data/state.db-wal data/state.db-shm   # иначе старый WAL применится к восстановленному файлу
docker compose up -d
```

Что и почему растёт: `simhash_deduplicator` и `paraphrase_deduplicator`
удаляют записи старше своего `lookback_hours` при каждом сохранении, поэтому
не растут. Таблицы точных совпадений (`seen_hashes`, `seen_links`) растут
бессрочно — осознанно: это короткие строки (~190 байт), а репост старой
новости через месяц тоже стоит поймать. В штатном режиме это десятки строк
в сутки (единоразовый всплеск возможен при первом подключении старого
канала, когда читается его недавняя история).

Файл сессии Telethon (`data/*.session`) в бэкап БД **не входит** — это
секрет авторизации, и отправлять его в облако без шифрования не стоит.
Если он потеряется, придётся один раз заново ввести код подтверждения.

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
тестируются через `InMemoryStorage`/`InMemorySourceRepository`/
`InMemorySimhashIndex`/`InMemoryLemmaIndex` (см. `tests/support/`), а
`SqliteStorage`/`SqliteSourceRepository`/`SqliteSimhashIndex`/
`SqliteLemmaIndex` — отдельными тестами с временной БД (включая граничные
64-битные значения SimHash и изоляцию между индексами с разным
`table_name`). `paraphrase_deduplicator` тестируется в том числе на
реальных парах текстов (пересказ той же новости / независимая новость) —
без моков лемматизатора, pymorphy3 работает офлайн и детерминированно.
Бизнес-логика команд бота (`bot/commands.py`) тестируется без установленного
`python-telegram-bot` — она не зависит от этой библиотеки напрямую; сама
обвязка `bot/telegram_bot.py` — тонкий слой поверх неё.

## Известные ограничения

- `AdFilter` сравнивает точные подстроки нормализованного текста и не
  учитывает словоизменение (например, ключевое слово "скидка" не совпадёт
  со словоформой "скидкой"). Для лучшего покрытия перечисляйте нужные
  словоформы явно в `config.yaml`.
- Дедупликация по-прежнему не использует эмбеддинги (semantic similarity).
  `paraphrase_deduplicator` (лемматизация + Жаккар, см. раздел
  "Дедупликация") ловит типичный случай — пересказ той же новости другими
  словами, но с общими именами/терминами/цифрами. Два текста об одном и
  том же событии вообще без общих значимых слов он всё ещё не поймает, и
  работает только для русского языка (лемматизатор — pymorphy3).
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
