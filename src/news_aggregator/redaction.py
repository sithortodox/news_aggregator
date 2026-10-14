"""Маскировка секретов в тексте, который уходит за пределы процесса (логи, алерты)."""

from __future__ import annotations

import re

# Токен Telegram-бота: <числовой id>:<35 символов base64url>.
BOT_TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")


def redact_secrets(text: str) -> str:
    """Заменяет токены ботов на <redacted>.

    Bot API кладёт токен прямо в URL (`/bot<token>/getUpdates`), и он легко
    попадает в текст исключений, а их тексты копируют в логи, чаты и тикеты.
    """
    return BOT_TOKEN_RE.sub("<redacted>", text)
