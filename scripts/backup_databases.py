#!/usr/bin/env python3
"""
Backup automático dos databases Nanda

Estratégia:
- Backup usando SQLite .backup() API (consistente e atômico)
- Compressão gzip para economia de espaço
- Rotação: últimos 30 dias (daily) + 12 meses (monthly)
- Verificação de integridade antes e depois
- Checksum SHA256 para validação

Uso:
    python scripts/backup_databases.py
"""

import os
import sys
import gzip
import shutil
import sqlite3
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuração
SCRIPT_DIR = Path(__file__).parent
BACKEND_DIR = SCRIPT_DIR.parent
BACKUP_DIR = BACKEND_DIR.parent / "backups"

DATABASES = [
    BACKEND_DIR / "crm.db",
    BACKEND_DIR / "crm-agent.db"
]

# Retenção
DAILY_RETENTION_DAYS = 30
MONTHLY_RETENTION_MONTHS = 12


def checkpoint_wal(db_path: Path):
    """
    Checkpoint do WAL antes do backup
    Consolida transações pendentes no arquivo principal
    """
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        logger.info(f"✅ WAL checkpoint: {db_path.name} - {result}")
        return True
    except Exception as e:
        logger.warning(f"⚠️  WAL checkpoint failed for {db_path.name}: {e}")
        return False


def verify_integrity(db_path: Path) -> bool:
    """
    Verifica integridade do database usando PRAGMA integrity_check
    """
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]
        cursor.close()
        conn.close()

        if result == "ok":
            logger.info(f"✅ Integrity check passed: {db_path.name}")
            return True
        else:
            logger.error(f"❌ Integrity check FAILED: {db_path.name} - {result}")
            return False
    except Exception as e:
        logger.error(f"❌ Integrity check ERROR: {db_path.name} - {e}")
        return False


def calculate_checksum(file_path: Path) -> str:
    """Calcula SHA256 do arquivo"""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def backup_database(db_path: Path, backup_type: str = "daily") -> bool:
    """
    Realiza backup de um database

    Args:
        db_path: Caminho do database
        backup_type: "daily" ou "monthly"

    Returns:
        True se sucesso, False se erro
    """
    if not db_path.exists():
        logger.warning(f"⚠️  Database not found: {db_path}")
        return False

    db_name = db_path.stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Diretórios de backup
    backup_subdir = BACKUP_DIR / backup_type
    backup_subdir.mkdir(parents=True, exist_ok=True)

    # Arquivos
    backup_filename = f"{db_name}_{timestamp}.db"
    backup_path = backup_subdir / backup_filename
    compressed_path = backup_subdir / f"{backup_filename}.gz"
    checksum_path = backup_subdir / f"{backup_filename}.sha256"

    try:
        logger.info(f"📦 Starting backup: {db_path.name}")

        # 1. Checkpoint WAL
        checkpoint_wal(db_path)

        # 2. Verificar integridade ANTES do backup
        if not verify_integrity(db_path):
            logger.error(f"❌ Backup aborted: database corrupted: {db_path}")
            return False

        # 3. Backup usando SQLite .backup() API (consistente e atômico)
        logger.info(f"   Copying database...")
        source_conn = sqlite3.connect(str(db_path))
        backup_conn = sqlite3.connect(str(backup_path))

        with backup_conn:
            source_conn.backup(backup_conn)

        source_conn.close()
        backup_conn.close()

        # 4. Verificar integridade do backup
        if not verify_integrity(backup_path):
            logger.error(f"❌ Backup corrupted: {backup_path}")
            backup_path.unlink()
            return False

        # 5. Calcular checksum
        logger.info(f"   Calculating checksum...")
        checksum = calculate_checksum(backup_path)
        checksum_path.write_text(checksum)

        # 6. Comprimir
        logger.info(f"   Compressing...")
        with open(backup_path, 'rb') as f_in:
            with gzip.open(compressed_path, 'wb', compresslevel=9) as f_out:
                shutil.copyfileobj(f_in, f_out)

        # 7. Remover arquivo não comprimido
        backup_path.unlink()

        # 8. Estatísticas
        original_size = db_path.stat().st_size
        compressed_size = compressed_path.stat().st_size
        ratio = (1 - compressed_size / original_size) * 100

        logger.info(f"✅ Backup completed: {compressed_path.name}")
        logger.info(f"   Original: {original_size / 1024:.1f} KB")
        logger.info(f"   Compressed: {compressed_size / 1024:.1f} KB")
        logger.info(f"   Compression: {ratio:.1f}%")
        logger.info(f"   Checksum: {checksum[:16]}...")

        return True

    except Exception as e:
        logger.error(f"❌ Backup failed: {db_path} - {e}")
        # Limpar arquivos parciais
        for path in [backup_path, compressed_path, checksum_path]:
            if path.exists():
                path.unlink()
        return False


def rotate_backups():
    """
    Rotaciona backups antigos segundo política de retenção
    - Daily: últimos 30 dias
    - Monthly: últimos 12 meses
    """
    logger.info("🔄 Rotating old backups...")
    now = datetime.now()
    removed_count = 0

    # Daily backups
    daily_dir = BACKUP_DIR / "daily"
    if daily_dir.exists():
        for backup_file in daily_dir.glob("*.db.gz"):
            try:
                # Extrair timestamp do nome (formato: nanda_20241217_120000.db.gz)
                parts = backup_file.stem.replace('.db', '').split('_')
                if len(parts) < 3:
                    continue

                timestamp_str = parts[-2] + parts[-1]  # YYYYMMDDHHMMSS
                backup_date = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
                age_days = (now - backup_date).days

                if age_days > DAILY_RETENTION_DAYS:
                    logger.info(f"   Removing old daily backup: {backup_file.name} ({age_days} days)")
                    backup_file.unlink()

                    # Remover checksum também
                    checksum_file = backup_file.with_suffix('')
                    checksum_file = checksum_file.with_suffix('.sha256')
                    if checksum_file.exists():
                        checksum_file.unlink()

                    removed_count += 1
            except Exception as e:
                logger.warning(f"   Error processing {backup_file.name}: {e}")
                continue

    # Monthly backups
    monthly_dir = BACKUP_DIR / "monthly"
    if monthly_dir.exists():
        for backup_file in monthly_dir.glob("*.db.gz"):
            try:
                parts = backup_file.stem.replace('.db', '').split('_')
                if len(parts) < 3:
                    continue

                timestamp_str = parts[-2] + parts[-1]
                backup_date = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
                age_months = (now.year - backup_date.year) * 12 + (now.month - backup_date.month)

                if age_months > MONTHLY_RETENTION_MONTHS:
                    logger.info(f"   Removing old monthly backup: {backup_file.name} ({age_months} months)")
                    backup_file.unlink()

                    checksum_file = backup_file.with_suffix('')
                    checksum_file = checksum_file.with_suffix('.sha256')
                    if checksum_file.exists():
                        checksum_file.unlink()

                    removed_count += 1
            except Exception as e:
                logger.warning(f"   Error processing {backup_file.name}: {e}")
                continue

    if removed_count > 0:
        logger.info(f"✅ Removed {removed_count} old backup(s)")
    else:
        logger.info(f"✅ No old backups to remove")


def is_first_day_of_month() -> bool:
    """Verifica se hoje é primeiro dia do mês"""
    return datetime.now().day == 1


def main():
    """Executar backup"""
    logger.info("=" * 70)
    logger.info("📦 NANDA DATABASE BACKUP")
    logger.info("=" * 70)

    # Tipo de backup
    backup_type = "monthly" if is_first_day_of_month() else "daily"
    logger.info(f"Backup type: {backup_type}")
    logger.info(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("")

    # Backup de cada database
    success_count = 0
    for db_path in DATABASES:
        if backup_database(db_path, backup_type):
            success_count += 1
        logger.info("")  # Linha em branco entre backups

    # Rotação
    rotate_backups()

    # Resumo
    logger.info("")
    logger.info("=" * 70)
    if success_count == len(DATABASES):
        logger.info(f"✅ ALL BACKUPS COMPLETED SUCCESSFULLY ({success_count}/{len(DATABASES)})")
        logger.info("=" * 70)
        sys.exit(0)
    else:
        logger.error(f"❌ SOME BACKUPS FAILED ({success_count}/{len(DATABASES)})")
        logger.info("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    main()
