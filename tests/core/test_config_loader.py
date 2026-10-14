from pathlib import Path

import pytest

from news_aggregator.config.loader import ConfigError, load_app_config, load_env_secrets
from news_aggregator.core.models import SourceKind

VALID_YAML = """
pipeline:
  dry_run: true
  poll_interval_seconds: 30
  max_messages_per_source: 50

sources:
  - id: demo
    kind: fake
    identifier: demo
    display_name: "Demo"

filters:
  - name: length_filter
    params:
      min_length: 15

deduplicators:
  - name: text_hash_deduplicator

enrichers: []

publishers:
  - name: console_publisher
    params:
      prefix: "[X]"

storage:
  name: sqlite
  params:
    db_path: data/state.db
"""


def _write(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def test_load_valid_config(tmp_path: Path) -> None:
    config_path = _write(tmp_path, VALID_YAML)

    config = load_app_config(config_path)

    assert config.pipeline.dry_run is True
    assert config.pipeline.poll_interval_seconds == 30
    assert len(config.sources) == 1
    assert config.sources[0].kind == SourceKind.FAKE
    assert config.filters[0].name == "length_filter"
    assert config.filters[0].params == {"min_length": 15}
    assert config.publishers[0].name == "console_publisher"
    assert config.storage.name == "sqlite"


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_app_config(tmp_path / "does_not_exist.yaml")


def test_invalid_yaml_raises_config_error(tmp_path: Path) -> None:
    config_path = _write(tmp_path, "sources: [this is not: valid: yaml: at all")

    with pytest.raises(ConfigError):
        load_app_config(config_path)


def test_missing_publishers_raises_config_error(tmp_path: Path) -> None:
    yaml_without_publishers = VALID_YAML.replace(
        'publishers:\n  - name: console_publisher\n    params:\n      prefix: "[X]"',
        "publishers: []",
    )
    config_path = _write(tmp_path, yaml_without_publishers)

    with pytest.raises(ConfigError):
        load_app_config(config_path)


def test_unknown_source_kind_raises_config_error(tmp_path: Path) -> None:
    bad_yaml = VALID_YAML.replace("kind: fake", "kind: not_a_real_kind")
    config_path = _write(tmp_path, bad_yaml)

    with pytest.raises(ConfigError):
        load_app_config(config_path)


def test_short_string_source_becomes_telegram_channel(tmp_path: Path) -> None:
    yaml_text = """
sources:
  - "@meduzalive"

publishers:
  - name: console_publisher
"""
    config_path = _write(tmp_path, yaml_text)

    config = load_app_config(config_path)

    assert len(config.sources) == 1
    source = config.sources[0]
    assert source.kind == SourceKind.TELEGRAM_CHANNEL
    assert source.identifier == "@meduzalive"
    assert source.id == "meduzalive"
    assert source.display_name == "@meduzalive"
    assert source.enabled is True


def test_partial_dict_source_fills_in_defaults(tmp_path: Path) -> None:
    yaml_text = """
sources:
  - identifier: "@some_group"
    kind: telegram_group
  - identifier: "@paused_channel"
    enabled: false

publishers:
  - name: console_publisher
"""
    config_path = _write(tmp_path, yaml_text)

    config = load_app_config(config_path)

    assert config.sources[0].kind == SourceKind.TELEGRAM_GROUP
    assert config.sources[0].id == "some_group"
    assert config.sources[1].enabled is False
    assert config.sources[1].id == "paused_channel"


def test_duplicate_channel_identifier_raises_config_error(tmp_path: Path) -> None:
    yaml_text = """
sources:
  - "@news"
  - "@news"

publishers:
  - name: console_publisher
"""
    config_path = _write(tmp_path, yaml_text)

    with pytest.raises(ConfigError):
        load_app_config(config_path)


def test_id_collision_gets_numeric_suffix(tmp_path: Path) -> None:
    yaml_text = """
sources:
  - "@news"
  - identifier: "@news_other"
    id: news

publishers:
  - name: console_publisher
"""
    config_path = _write(tmp_path, yaml_text)

    config = load_app_config(config_path)

    assert [s.id for s in config.sources] == ["news", "news_2"]


def test_source_dict_without_identifier_raises_config_error(tmp_path: Path) -> None:
    yaml_text = """
sources:
  - kind: telegram_channel

publishers:
  - name: console_publisher
"""
    config_path = _write(tmp_path, yaml_text)

    with pytest.raises(ConfigError):
        load_app_config(config_path)


def test_load_env_secrets_defaults_when_no_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    monkeypatch.delenv("TELEGRAM_TARGET_CHANNEL", raising=False)

    secrets = load_env_secrets(dotenv_path=tmp_path / "nonexistent.env")

    assert secrets.api_id is None
    assert secrets.api_hash is None
    assert secrets.session_name == "news_aggregator"


# ---------------------------------------------------------------------- alerts


def _alerts_config(tmp_path: Path, alerts_block: str) -> Path:
    return _write(tmp_path, VALID_YAML + alerts_block)


def test_alerts_section_is_optional_and_defaults_apply(tmp_path: Path) -> None:
    config = load_app_config(_write(tmp_path, VALID_YAML))

    assert config.alerts.enabled is True
    assert config.alerts.source_failure_threshold == 5
    assert config.alerts.publish_failure_threshold == 1
    assert config.alerts.flood_wait_alert_seconds == 300.0
    assert config.alerts.cooldown_seconds == 3600.0


def test_alerts_section_overrides_values(tmp_path: Path) -> None:
    config = load_app_config(
        _alerts_config(
            tmp_path,
            """
alerts:
  enabled: false
  source_failure_threshold: 3
  publish_failure_threshold: 2
  flood_wait_alert_seconds: 120
  cooldown_seconds: 600.5
""",
        )
    )

    assert config.alerts.enabled is False
    assert config.alerts.source_failure_threshold == 3
    assert config.alerts.publish_failure_threshold == 2
    assert config.alerts.flood_wait_alert_seconds == 120.0
    assert config.alerts.cooldown_seconds == 600.5


def test_alerts_partial_section_keeps_other_defaults(tmp_path: Path) -> None:
    config = load_app_config(
        _alerts_config(tmp_path, "\nalerts:\n  source_failure_threshold: 10\n")
    )

    assert config.alerts.source_failure_threshold == 10
    assert config.alerts.cooldown_seconds == 3600.0


def test_alerts_unknown_key_is_an_error_not_silently_ignored(tmp_path: Path) -> None:
    """Опечатка в настройке защитного механизма не должна молча оставлять дефолт."""
    path = _alerts_config(tmp_path, "\nalerts:\n  source_failure_treshold: 3\n")

    with pytest.raises(ConfigError, match="source_failure_treshold"):
        load_app_config(path)


@pytest.mark.parametrize(
    "block",
    [
        "alerts:\n  source_failure_threshold: 0\n",
        "alerts:\n  source_failure_threshold: -1\n",
        "alerts:\n  source_failure_threshold: 2.5\n",
        "alerts:\n  source_failure_threshold: true\n",
        "alerts:\n  source_failure_threshold: 'пять'\n",
        "alerts:\n  publish_failure_threshold: 0\n",
        "alerts:\n  flood_wait_alert_seconds: 0\n",
        "alerts:\n  flood_wait_alert_seconds: -5\n",
        "alerts:\n  cooldown_seconds: 0\n",
        "alerts:\n  cooldown_seconds: false\n",
        "alerts:\n  enabled: 'yes'\n",
        "alerts:\n  enabled: 1\n",
        "alerts: [1, 2]\n",
    ],
)
def test_alerts_invalid_values_raise_config_error(tmp_path: Path, block: str) -> None:
    with pytest.raises(ConfigError):
        load_app_config(_alerts_config(tmp_path, "\n" + block))
