# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Сначала только файлы, нужные для установки зависимостей — так слой с
# зависимостями кэшируется и не пересобирается при каждом изменении кода.
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

# Конфигурация копируется в образ как значение по умолчанию; в проде обычно
# монтируется поверх volume-ом (см. docker-compose.yml), чтобы менять
# список каналов без пересборки образа.
COPY config ./config

# Непривилегированный пользователь: контейнер не должен работать от root.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin aggregator \
    && mkdir -p /app/data \
    && chown -R aggregator:aggregator /app

USER aggregator

# Состояние (SQLite + сессия Telethon) должно жить в volume, а не в слое
# контейнера — иначе оно потеряется при пересоздании контейнера.
VOLUME ["/app/data"]

ENTRYPOINT ["python", "-m", "news_aggregator.main"]
CMD ["--config", "config/config.yaml"]

# Раз в минуту проверяем heartbeat-файл, который основной цикл обновляет
# после каждого run_once() (см. news_aggregator/healthcheck.py). Порог
# свежести читается из смонтированного config.yaml динамически
# (poll_interval_seconds + запас), так что start-period ниже — это только
# грация на первый прогон при старте контейнера, а не сам порог свежести.
# Статус проверяется через `docker compose ps` / `docker inspect`; сам по
# себе HEALTHCHECK не перезапускает контейнер (restart-политика реагирует
# на завершение процесса, а не на unhealthy) — подробности в README.
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "-m", "news_aggregator.healthcheck", "--config", "config/config.yaml"]
