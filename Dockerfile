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
