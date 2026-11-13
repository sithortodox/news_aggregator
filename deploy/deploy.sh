#!/usr/bin/env bash
# Обновление агрегатора на VPS: код, проверка конфига, пересборка, уборка.
#
#   ~/news_aggregator/deploy/deploy.sh
#
# 1. git pull (только fast-forward: локальные правки кода не затираются;
#    config/config.yaml на сервере правится и не коммитится — pull его не трогает,
#    пока в репозитории не меняется тот же файл).
# 2. Проверка конфига на НОВОМ образе (--build обязателен, см. README).
# 3. Перезапуск контейнера на новом образе.
# 4. Уборка: старый образ становится «висячим», а каждая сборка оставляет
#    кэш слоёв — на диске 10 ГБ после пары выкаток это сотни мегабайт.
#    Удаляется только неиспользуемое: работающие образы и контейнеры
#    (в том числе соседние, вроде mtproxymax) не трогаются.
#
# Удобно запускать через nohup, чтобы обрыв SSH не прервал сборку:
#   nohup ~/news_aggregator/deploy/deploy.sh > /tmp/deploy.log 2>&1 &

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

git pull --ff-only
git log --oneline -1

docker compose run --rm --build news-aggregator --config config/config.yaml --validate-config
docker compose up -d --build news-aggregator

docker image prune -f
docker builder prune -f

df -h / | tail -1
echo DEPLOY_OK
