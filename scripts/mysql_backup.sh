#!/usr/bin/env bash

# This script is scheduled to run daily via cron for full-scale MySQL database hot backups.
# It reads database configuration from the .env file.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load environment variables from .env
if [ -f "$PROJECT_ROOT/.env" ]; then
    export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs)
else
    echo "Error: .env file not found in $PROJECT_ROOT"
    exit 1
fi

BACKUP_DIR="/var/backups/match_bot"
DATE_FORMAT=$(date +"%Y-%m-%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/match_bot_backup_${DATE_FORMAT}.sql"

# Docker container identification parameters
CONTAINER_NAME="match_mysql_db"
# Use environment variables loaded from .env
MYSQL_USER="${DB_USER}"
MYSQL_PASSWORD="${DB_PASSWORD}"
MYSQL_DATABASE="${DB_NAME}"

# Create storage backup path
mkdir -p "$BACKUP_DIR"

echo "=== Starting database backup run at $(date) ==="

# Execute safe mysqldump command on active Docker volume
docker exec "$CONTAINER_NAME" mysqldump \
    -u"$MYSQL_USER" \
    -p"$MYSQL_PASSWORD" \
    "$MYSQL_DATABASE" > "$BACKUP_FILE"

# Compress resulting sql script to reduce file-system space overheads
if [ $? -eq 0 ]; then
    gzip "$BACKUP_FILE"
    echo "Backup completed successfully! Location: ${BACKUP_FILE}.gz"
    
    # Remove files older than 30 days to enforce cleanup
    find "$BACKUP_DIR" -type f -name "*.sql.gz" -mtime +30 -delete
    echo "Cleanup of historical backups (30 days limit) done."
else
    echo "CRITICAL: Backup process returned error exit status! Check database health states."
    exit 1
fi
