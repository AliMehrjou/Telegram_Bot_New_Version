#!/usr/bin/env bash
set -euo pipefail

# This script is scheduled to run daily via cron for full-scale MySQL database
# hot backups.
#
# FIX PHASE1-CRIT-18 (3 issues):
#   1. CONTAINER_NAME was "match_mysql_db" but docker-compose.yml defines the
#      primary MySQL service as "match_mysql_primary". The script silently
#      failed every cron run.
#   2. Hardcoded DB credentials (match_bot_user / match_bot_password) bypassed
#      the .env file. Now reads from environment with safe fallbacks.
#   3. mysqldump was missing --single-transaction (table locks on InnoDB),
#      --routines, --triggers, and --master-data=2 (binlog position for
#      point-in-time recovery). DEPLOYMENT.md had a different inline recipe
#      with these flags — the two recipes have been unified.

BACKUP_DIR="${BACKUP_DIR:-/var/backups/match_bot}"
DATE_FORMAT=$(date +"%Y-%m-%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/match_bot_backup_${DATE_FORMAT}.sql"

# Docker container identification — must match docker-compose.yml service name.
CONTAINER_NAME="${MYSQL_CONTAINER_NAME:-match_mysql_primary}"

# Read DB credentials from environment (fallback to legacy defaults for
# backward compatibility, but log a warning so operators fix their env).
MYSQL_USER="${MYSQL_USER:-match_bot_user}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-}"
MYSQL_DATABASE="${MYSQL_DATABASE:-match_bot_db}"

if [ -z "$MYSQL_PASSWORD" ]; then
    echo "WARNING: MYSQL_PASSWORD is not set in environment. Trying legacy default." >&2
    MYSQL_PASSWORD="match_bot_password"
fi

# Create storage backup path
mkdir -p "$BACKUP_DIR"

echo "=== Starting database backup run at $(date) ==="
echo "Container: $CONTAINER_NAME | Database: $MYSQL_DATABASE | Target: $BACKUP_FILE"

# Execute safe mysqldump command on active Docker volume.
#   --single-transaction  → InnoDB-consistent snapshot without table locks
#   --routines --triggers → include stored procedures and triggers
#   --master-data=2       → record binlog position as a comment (for PITR)
#   --set-gtid-purged=OFF → avoid breaking replica setup if GTID is enabled later
#   --quick               → stream rows instead of buffering whole table in RAM
#   --hex-blob            → deterministic dumps of BLOB columns
if ! docker exec "$CONTAINER_NAME" mysqldump \
        --single-transaction \
        --routines \
        --triggers \
        --master-data=2 \
        --set-gtid-purged=OFF \
        --quick \
        --hex-blob \
        -u"$MYSQL_USER" \
        -p"$MYSQL_PASSWORD" \
        "$MYSQL_DATABASE" > "$BACKUP_FILE" 2> "${BACKUP_FILE}.err"; then
    echo "CRITICAL: Backup process returned error exit status!" >&2
    echo "--- mysqldump stderr ---" >&2
    cat "${BACKUP_FILE}.err" >&2
    rm -f "$BACKUP_FILE" "${BACKUP_FILE}.err"
    exit 1
fi
rm -f "${BACKUP_FILE}.err"

# Compress resulting sql script to reduce file-system space overheads
gzip "$BACKUP_FILE"
echo "Backup completed successfully! Location: ${BACKUP_FILE}.gz"

# Remove files older than 30 days to enforce cleanup
find "$BACKUP_DIR" -type f -name "*.sql.gz" -mtime +30 -delete
echo "Cleanup of historical backups (30 days limit) done."

echo "=== Backup run finished at $(date) ==="
