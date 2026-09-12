"""Создание Telethon-клиента из секретов, загруженных из .env.

Единственная точка, где секреты Telegram (api_id/api_hash) превращаются
в реальный клиент. Секреты сюда попадают только через TelegramSecrets
(см. news_aggregator.config.loader.load_env_secrets) — никогда напрямую
из YAML-конфигурации.
"""

from __future__ import annotations

from pathlib import Path

from telethon import TelegramClient

from news_aggregator.config.loader import TelegramSecrets


class TelegramSecretsMissingError(RuntimeError):
    """Поднимается, если TELEGRAM_API_ID/TELEGRAM_API_HASH не заданы в .env."""


def build_telegram_client(secrets: TelegramSecrets) -> TelegramClient:
    """Строит (но не подключает) TelegramClient на основе секретов из .env.

    Подключение (client.start() / client.connect()) — ответственность
    вызывающего кода (main.py), чтобы этот модуль оставался простой фабрикой.
    """
    if secrets.api_id is None or secrets.api_hash is None:
        raise TelegramSecretsMissingError(
            "Заполните TELEGRAM_API_ID и TELEGRAM_API_HASH в .env "
            "(см. .env.example) перед запуском с реальным Telegram. "
            "Для работы без Telegram используйте источник kind: fake."
        )

    # session_name может быть путём (например "data/news_aggregator"), чтобы
    # файл сессии Telethon пережил перезапуск Docker-контейнера через тот же
    # volume, что и SQLite-хранилище. Создаём родительский каталог заранее.
    session_path = Path(secrets.session_name)
    if session_path.parent and str(session_path.parent) not in (".", ""):
        session_path.parent.mkdir(parents=True, exist_ok=True)

    return TelegramClient(secrets.session_name, secrets.api_id, secrets.api_hash)
