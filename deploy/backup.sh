#!/usr/bin/env bash
# Бэкап БД состояния агрегатора (запускать из cron на хосте VPS).
#
# 1. Создаёт сжатую консистентную копию БД через контейнер (без остановки)
#    в ./data/backups и оставляет последние BACKUP_KEEP копий.
# 2. Если задан BACKUP_REMOTE — копирует бэкапы за пределы сервера через
#    rclone и удаляет там копии старше BACKUP_REMOTE_MAX_AGE.
#
# Переменные окружения (все необязательны):
#   BACKUP_KEEP             сколько копий хранить локально (по умолчанию 7)
#   BACKUP_REMOTE           назначение rclone, например "gdrive:news-aggregator-backups"
#   BACKUP_REMOTE_MAX_AGE   срок хранения на удалённой стороне (по умолчанию 30d)
#
# Пример cron (ежедневно в 03:17, вывод — в syslog, а не в растущий файл):
#   17 3 * * * BACKUP_REMOTE=gdrive:news-aggregator-backups \
#       /home/deploy/news_aggregator/deploy/backup.sh 2>&1 | logger -t news-aggregator-backup
#
# Почему именно rclone COPY, а не SYNC: sync зеркалит удаления, и если локальный
# каталог бэкапов случайно окажется пустым, он вычистит и удалённые копии.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

KEEP="${BACKUP_KEEP:-7}"

docker compose exec -T news-aggregator \
    python -m news_aggregator.maintenance backup --keep "${KEEP}"

if [[ -n "${BACKUP_REMOTE:-}" ]]; then
    if ! command -v rclone >/dev/null 2>&1; then
        echo "BACKUP_REMOTE задан, но rclone не установлен" >&2
        exit 1
    fi
    rclone copy ./data/backups "${BACKUP_REMOTE}" --include "state-*.db"
    rclone delete "${BACKUP_REMOTE}" --include "state-*.db" \
        --min-age "${BACKUP_REMOTE_MAX_AGE:-30d}"
    echo "Бэкапы скопированы в ${BACKUP_REMOTE}"
else
    echo "BACKUP_REMOTE не задан: копии остались только на этом сервере" >&2
fi
