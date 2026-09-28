#!/usr/bin/env bash
# Бэкап БД состояния агрегатора (запускать из cron на хосте VPS).
#
# 1. Создаёт сжатую консистентную копию БД через контейнер (без остановки)
#    в ./data/backups и удаляет копии старше BACKUP_MAX_AGE_HOURS (самая
#    свежая копия не удаляется никогда).
# 2. Если задан BACKUP_REMOTE — копирует бэкапы за пределы сервера через
#    rclone и удаляет там копии старше BACKUP_REMOTE_MAX_AGE.
#
# Переменные окружения (все необязательны):
#   BACKUP_MAX_AGE_HOURS    хранить локальные копии не дольше N часов (по умолчанию 24;
#                           0 — не удалять по возрасту)
#   BACKUP_KEEP             максимум копий локально (по умолчанию 7)
#   BACKUP_REMOTE           назначение rclone, например "gdrive:news-aggregator-backups"
#   BACKUP_REMOTE_MAX_AGE   срок хранения на удалённой стороне, формат rclone
#                           ("24h", "7d"; по умолчанию 24h)
#
# Пример cron (каждые 6 часов, вывод — в syslog, а не в растущий файл):
#   17 */6 * * * BACKUP_REMOTE=gdrive:news-aggregator-backups \
#       /home/deploy/news_aggregator/deploy/backup.sh 2>&1 | logger -t news-aggregator-backup
#
# Почему именно rclone COPY, а не SYNC: sync зеркалит удаления, и если локальный
# каталог бэкапов случайно окажется пустым, он вычистит и удалённые копии.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

KEEP="${BACKUP_KEEP:-7}"
MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-24}"

docker compose exec -T news-aggregator \
    python -m news_aggregator.maintenance backup --keep "${KEEP}" \
    --max-age-hours "${MAX_AGE_HOURS}"

if [[ -n "${BACKUP_REMOTE:-}" ]]; then
    if ! command -v rclone >/dev/null 2>&1; then
        echo "BACKUP_REMOTE задан, но rclone не установлен" >&2
        exit 1
    fi
    rclone copy ./data/backups "${BACKUP_REMOTE}" --include "state-*.db"
    rclone delete "${BACKUP_REMOTE}" --include "state-*.db" \
        --min-age "${BACKUP_REMOTE_MAX_AGE:-24h}"
    echo "Бэкапы скопированы в ${BACKUP_REMOTE}"
else
    echo "BACKUP_REMOTE не задан: копии остались только на этом сервере" >&2
fi
