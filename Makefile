# Makefile — краткие команды для разработки, проверки и деплоя.
# Запуск: make <цель>, например `make test`.

.PHONY: help install lint format typecheck test check run run-once \
        validate-config docker-build docker-up docker-down docker-logs \
        docker-run-once clean

help:
	@echo "Доступные команды:"
	@echo "  make install          — установить проект и dev-зависимости (pip install -e '.[dev]')"
	@echo "  make lint             — проверить стиль кода (ruff check)"
	@echo "  make format           — автоформатирование (ruff format)"
	@echo "  make typecheck        — проверка типов (mypy)"
	@echo "  make test             — прогнать тесты (pytest)"
	@echo "  make check            — lint + typecheck + test (всё, что гоняет CI)"
	@echo "  make validate-config  — проверить config.yaml/.env без запуска пайплайна"
	@echo "  make run-once         — один проход по источникам (dry-run из config.yaml)"
	@echo "  make run              — запуск в режиме постоянного опроса (Ctrl+C для остановки)"
	@echo "  make docker-build     — собрать Docker-образ"
	@echo "  make docker-up        — поднять контейнер в фоне (docker compose up -d)"
	@echo "  make docker-down      — остановить контейнер"
	@echo "  make docker-logs      — смотреть логи контейнера"
	@echo "  make docker-run-once  — интерактивный разовый запуск в контейнере (для первого входа в Telegram)"
	@echo "  make clean            — удалить кэши/артефакты сборки (НЕ трогает data/ и .env)"

install:
	pip install -e ".[dev]"

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy src

test:
	pytest -v

check: lint typecheck test

validate-config:
	python -m news_aggregator.main --config config/config.yaml --validate-config

run-once:
	python -m news_aggregator.main --config config/config.yaml --once --verbose

run:
	python -m news_aggregator.main --config config/config.yaml

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

docker-run-once:
	docker compose run --rm news-aggregator --config config/config.yaml --once

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -rf .mypy_cache .ruff_cache .pytest_cache *.egg-info src/*.egg-info build dist
