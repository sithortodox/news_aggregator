import pytest

from news_aggregator.core.registry import ComponentNotRegisteredError, ComponentRegistry


class _DummyFilter:
    def __init__(self, min_length: int = 10) -> None:
        self.min_length = min_length


def test_register_and_create_component_by_name() -> None:
    registry = ComponentRegistry()
    registry.filters.register("dummy", _DummyFilter)

    instance = registry.filters.create("dummy", min_length=5)

    assert isinstance(instance, _DummyFilter)
    assert instance.min_length == 5


def test_create_unknown_component_raises_with_available_list() -> None:
    registry = ComponentRegistry()
    registry.filters.register("dummy", _DummyFilter)

    with pytest.raises(ComponentNotRegisteredError) as exc_info:
        registry.filters.create("unknown")

    assert exc_info.value.available == ["dummy"]


def test_categories_are_isolated() -> None:
    registry = ComponentRegistry()
    registry.filters.register("shared_name", _DummyFilter)

    with pytest.raises(ComponentNotRegisteredError):
        registry.deduplicators.create("shared_name")


def test_reregistering_overwrites_factory() -> None:
    registry = ComponentRegistry()
    registry.filters.register("dummy", lambda: "first")
    registry.filters.register("dummy", lambda: "second")

    assert registry.filters.create("dummy") == "second"


def test_available_lists_registered_names_sorted() -> None:
    registry = ComponentRegistry()
    registry.publishers.register("console", object)
    registry.publishers.register("telegram", object)

    assert registry.publishers.available() == ["console", "telegram"]
