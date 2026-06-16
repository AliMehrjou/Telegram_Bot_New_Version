#!/usr/bin/env bash

# This script is scheduled to run daily via cron for full-scale MySQL database hot backups.
# Configuration settings are extracted in-situ or loaded from environment variables.

BACKUP_DIR="/var/backups/match_bot"
DATE_FORMAT=$(date +"%Y-%m-%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/match_bot_backup_${DATE_FORMAT}.sql"

# Docker container identification parameters
CONTAINER_NAME="match_mysql_db"
MYSQL_USER="match_bot_user"
MYSQL_PASSWORD="match_bot_password"
MYSQL_DATABASE="match_bot_db"

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
