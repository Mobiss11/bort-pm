#!/usr/bin/env bash
# Бэкап БД «Борт» через sqlite3 .backup (корректно при WAL).
# Использование: scripts/backup.sh
# Переменные: BORT_DB (путь к БД), BORT_BACKUP_DIR (куда класть копии).
# Хранит 14 последних копий. Код возврата: 0 — успех, иначе ошибка.
set -euo pipefail

DB_PATH="${BORT_DB:-$HOME/bort/data/bort.db}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${BORT_BACKUP_DIR:-$SCRIPT_DIR/../backups}"

[ -f "$DB_PATH" ] || { echo "БД не найдена: $DB_PATH" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/bort-$STAMP.db"
# Защита от коллизии имени внутри одной секунды
while [ -e "$OUT" ]; do
    sleep 1
    STAMP="$(date +%Y%m%d-%H%M%S)"
    OUT="$BACKUP_DIR/bort-$STAMP.db"
done

sqlite3 "$DB_PATH" ".backup '$OUT'"

# Проверка копии: открывается и целостна
CHECK="$(sqlite3 "$OUT" "PRAGMA integrity_check;")"
[ "$CHECK" = "ok" ] || { echo "Копия повреждена ($OUT): $CHECK" >&2; exit 1; }

# wal/shm от временных открытий копии не нужны: файл .backup самодостаточен
rm -f "$OUT-wal" "$OUT-shm"

# Храним 14 последних копий
ls -1t "$BACKUP_DIR"/bort-*.db 2>/dev/null | tail -n +15 | xargs rm -f --

echo "$OUT"
