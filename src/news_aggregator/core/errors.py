"""Исключения, составляющие контракт между пайплайном и его адаптерами."""

from __future__ import annotations


class RateLimitedError(Exception):
    """Адаптер получил от внешнего сервиса требование подождать.

    Для Telegram это FloodWaitError. Адаптеры (читатели и издатели) переводят
    сервис-специфичное исключение в это, чтобы ядро могло отреагировать,
    ничего не зная про Telegram: приостановить обращения на нужное время,
    не засчитывать это как «источник недоступен» и не терять сообщение.

    Attributes:
        retry_after_seconds: Сколько секунд сервис просит подождать.
    """

    def __init__(self, retry_after_seconds: float, message: str | None = None) -> None:
        self.retry_after_seconds = max(float(retry_after_seconds), 0.0)
        super().__init__(
            message or f"Превышен лимит запросов, повторить через {self.retry_after_seconds:.0f} с"
        )
