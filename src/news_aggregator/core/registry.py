"""Реестр компонентов.

ComponentRegistry позволяет регистрировать фабрики компонентов под
строковыми именами (например, "length_filter" или "sqlite_storage") и
затем создавать их экземпляры по конфигурации, не изменяя код пайплайна.

Новый фильтр/дедупликатор/издатель/обогатитель/источник добавляется так:
    1. Реализовать соответствующий интерфейс в своём модуле.
    2. Зарегистрировать фабрику в ComponentRegistry (обычно в bootstrap-коде
       приложения, см. news_aggregator.main).
    3. Указать имя компонента и его параметры в config.yaml.

Пайплайн при этом не меняется.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")

# Фабрика компонента: принимает произвольные параметры из конфигурации
# (распакованные как **kwargs) и возвращает готовый экземпляр компонента.
ComponentFactory = Callable[..., T]


class ComponentNotRegisteredError(KeyError):
    """Поднимается, когда в конфигурации указан незарегистрированный компонент."""

    def __init__(self, category: str, name: str, available: list[str]) -> None:
        self.category = category
        self.name = name
        self.available = available
        super().__init__(
            f"Компонент '{name}' категории '{category}' не зарегистрирован. "
            f"Доступные: {available or '(пусто)'}"
        )


class TypedRegistry(Generic[T]):
    """Реестр фабрик одной категории компонентов (например, всех фильтров)."""

    def __init__(self, category: str) -> None:
        self._category = category
        self._factories: dict[str, ComponentFactory[T]] = {}

    def register(self, name: str, factory: ComponentFactory[T]) -> None:
        """Регистрирует фабрику под именем. Повторная регистрация того же
        имени перезаписывает предыдущую фабрику (полезно для тестов/моков)."""
        self._factories[name] = factory

    def create(self, name: str, **kwargs: object) -> T:
        """Создаёт экземпляр компонента по имени и параметрам конфигурации."""
        if name not in self._factories:
            raise ComponentNotRegisteredError(self._category, name, sorted(self._factories))
        return self._factories[name](**kwargs)

    def available(self) -> list[str]:
        """Возвращает список зарегистрированных имён в этой категории."""
        return sorted(self._factories)


class ComponentRegistry:
    """Центральный реестр всех категорий расширяемых компонентов."""

    def __init__(self) -> None:
        self.sources: TypedRegistry[object] = TypedRegistry("sources")
        self.filters: TypedRegistry[object] = TypedRegistry("filters")
        self.deduplicators: TypedRegistry[object] = TypedRegistry("deduplicators")
        self.enrichers: TypedRegistry[object] = TypedRegistry("enrichers")
        self.publishers: TypedRegistry[object] = TypedRegistry("publishers")
        self.storages: TypedRegistry[object] = TypedRegistry("storages")
        self.source_repositories: TypedRegistry[object] = TypedRegistry("source_repositories")
