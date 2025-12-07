# TeleMinion V2 - Migration & Backup Guide

This guide covers safe data migration and disaster recovery procedures.

---

## Table of Contents

1. [Volume Migration (Dev → Production)](#volume-migration)
2. [PostgreSQL Backup & Restore](#postgresql-backup--restore)
3. [MinIO Backup & Restore](#minio-backup--restore)
4. [Disaster Recovery](#disaster-recovery)
5. [Automated Backups](#automated-backups)

---

## Volume Migration

### Cold Migration (Recommended)

Use this procedure when moving TeleMinion from development to production.

#### Step 1: Stop the Application

```bash
# On development server
docker compose down
```

#### Step 2: Backup PostgreSQL

```bash
# Create backup directory
mkdir -p ~/teleminio-backup

# Backup PostgreSQL
docker run --rm \
  -v teleminio_postgres_data:/source:ro \
  -v ~/teleminio-backup:/backup \
  alpine tar czf /backup/postgres_data.tar.gz -C /source .

# Alternative: Use pg_dump for SQL backup (cleaner)
docker compose up -d postgres
docker compose exec postgres pg_dump -U teleminio teleminio > ~/teleminio-backup/teleminio.sql
docker compose down
```

#### Step 3: Backup MinIO Data

```bash
# Backup MinIO volumes
docker run --rm \
  -v teleminio_minio_data:/source:ro \
  -v ~/teleminio-backup:/backup \
  alpine tar czf /backup/minio_data.tar.gz -C /source .
```

#### Step 4: Transfer to Production

```bash
# Copy backups to production server
scp ~/teleminio-backup/*.tar.gz user@production:/tmp/

# Or for SQL dump
scp ~/teleminio-backup/teleminio.sql user@production:/tmp/
```

#### Step 5: Restore on Production

```bash
# On production server
cd /path/to/teleminio

# Create volumes first
docker volume create teleminio_postgres_data
docker volume create teleminio_minio_data
docker volume create teleminio_downloads_temp

# Restore PostgreSQL
docker run --rm \
  -v teleminio_postgres_data:/target \
  -v /tmp:/backup:ro \
  alpine tar xzf /backup/postgres_data.tar.gz -C /target

# Restore MinIO
docker run --rm \
  -v teleminio_minio_data:/target \
  -v /tmp:/backup:ro \
  alpine tar xzf /backup/minio_data.tar.gz -C /target

# Start application
docker compose up -d
```

#### Alternative: Restore from SQL Dump

```bash
# Start PostgreSQL only
docker compose up -d postgres

# Wait for it to be ready
sleep 10

# Restore from SQL dump
cat /tmp/teleminio.sql | docker compose exec -T postgres psql -U teleminio teleminio

# Start full application
docker compose up -d
```

---

## PostgreSQL Backup & Restore

### Manual Backup

```bash
# Create SQL dump
docker compose exec postgres pg_dump -U teleminio teleminio > backup_$(date +%Y%m%d_%H%M%S).sql

# Compressed backup
docker compose exec postgres pg_dump -U teleminio teleminio | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz
```

### Manual Restore

```bash
# Stop application (keep postgres running)
docker compose stop app

# Restore from backup
cat backup_YYYYMMDD_HHMMSS.sql | docker compose exec -T postgres psql -U teleminio teleminio

# Or from compressed
gunzip -c backup_YYYYMMDD_HHMMSS.sql.gz | docker compose exec -T postgres psql -U teleminio teleminio

# Restart application
docker compose up -d app
```

---

## MinIO Backup & Restore

### Using MinIO Client (mc)

```bash
# Install mc (MinIO Client)
wget https://dl.min.io/client/mc/release/linux-amd64/mc
chmod +x mc
sudo mv mc /usr/local/bin/

# Configure source MinIO
mc alias set source http://localhost:9000 minioadmin minioadmin

# Configure backup destination
mc alias set backup https://backup-minio.example.com backupadmin backupsecret

# Mirror all buckets to backup
mc mirror source/ backup/teleminio-mirror/

# Restore from backup
mc mirror backup/teleminio-mirror/ source/
```

### Volume-Level Backup

```bash
# Stop MinIO
docker compose stop minio

# Backup volume
docker run --rm \
  -v teleminio_minio_data:/source:ro \
  -v $(pwd)/backups:/backup \
  alpine tar czf /backup/minio_$(date +%Y%m%d).tar.gz -C /source .

# Restart MinIO
docker compose up -d minio
```

---

## Disaster Recovery

### Complete System Restore

1. **Clone repository on new server**
   ```bash
   git clone https://github.com/daintycodes/teleminion.git
   cd teleminion
   ```

2. **Create environment file**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

3. **Create Docker volumes**
   ```bash
   docker volume create teleminio_postgres_data
   docker volume create teleminio_minio_data
   docker volume create teleminio_downloads_temp
   ```

4. **Restore PostgreSQL backup**
   ```bash
   # Start postgres only
   docker compose up -d postgres
   sleep 15
   
   # Restore from backup
   cat backup.sql | docker compose exec -T postgres psql -U teleminio teleminio
   ```

5. **Restore MinIO backup**
   ```bash
   # Start minio
   docker compose up -d minio
   sleep 10
   
   # Restore using mc
   mc mirror backup/teleminio-mirror/ source/
   ```

6. **Start application**
   ```bash
   docker compose up -d
   ```

---

## Automated Backups

TeleMinion V2 includes an automated backup system that:

1. **Daily at midnight UTC**: Creates pg_dump
2. **Uploads to backup MinIO**: Separate instance for safety
3. **7-day retention**: Automatically deletes old backups

### Configuration

Set these environment variables:

```env
BACKUP_MINIO_ENDPOINT=backup-minio.example.com:9000
BACKUP_MINIO_ACCESS_KEY=backupadmin
BACKUP_MINIO_SECRET_KEY=backupsecret
BACKUP_MINIO_BUCKET=teleminio-backups
BACKUP_MINIO_SECURE=true
```

### Verify Backups

```bash
# List backups in backup MinIO
mc ls backup/teleminio-backups/

# Download specific backup
mc cp backup/teleminio-backups/teleminio_backup_20241206_000000.sql ./
```

---

## Coolify-Specific Notes

### Named Volumes in Coolify

The docker-compose.yaml uses explicitly named volumes:

```yaml
volumes:
  postgres_data:
    name: teleminio_postgres_data
  minio_data:
    name: teleminio_minio_data
```

This ensures volumes persist across Coolify redeployments.

### Before Redeployment

If making major changes that might affect volumes:

1. **Backup first** using methods above
2. **Redeploy** in Coolify
3. **Verify** data is intact

### Volume Location on Host

Coolify stores Docker volumes at:
```
/var/lib/docker/volumes/teleminio_postgres_data/_data
/var/lib/docker/volumes/teleminio_minio_data/_data
```

---

## Recovery Checklist

- [ ] Verify backup files exist and are not corrupted
- [ ] Stop all TeleMinion containers
- [ ] Restore PostgreSQL data
- [ ] Restore MinIO data
- [ ] Update .env with correct credentials
- [ ] Start containers
- [ ] Verify Telegram authentication works
- [ ] Verify MinIO buckets contain files
- [ ] Test file download/upload flow
