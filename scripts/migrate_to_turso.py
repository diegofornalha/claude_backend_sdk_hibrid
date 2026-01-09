#!/usr/bin/env python3
"""
Script de Migracao SQLite -> Turso

Migra todos os dados do SQLite para o banco Turso,
respeitando a ordem de foreign keys.

Uso:
    cd claude_backend_sdk_hibrid
    source venv/bin/activate
    python scripts/migrate_to_turso.py
"""

import asyncio
import os
import sys
from datetime import datetime
from typing import List, Dict, Any, Optional

# Adicionar path do projeto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite.connector
from dotenv import load_dotenv

# Carregar .env
load_dotenv()

# Configuracoes SQLite
SQLite_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'crm'),
    'port': int(os.getenv('DB_PORT', '3306'))
}

# Configurar Turso
os.environ['TURSO_DATABASE_URL'] = os.getenv(
    'TURSO_DATABASE_URL',
    'https://context-memory-diegofornalha.aws-us-east-1.turso.io'
)

# Ordem de migracao (respeitando FKs)
MIGRATION_ORDER = [
    'users',              # Base - sem FK
    'diagnosis_areas',    # Base - sem FK
    'questions',          # Base - sem FK
    'refresh_tokens',     # FK: users
    'mentor_invites',     # FK: users
    'clients',            # FK: users
    'reports',            # FK: users
    'hotspots',           # Base - sem FK
    'analysis_results',   # FK: reports
    'chat_sessions',      # FK: users
    'chat_messages',      # FK: chat_sessions, users
    'assessments',        # FK: clients
    'assessment_answers', # FK: assessments
    'assessment_area_scores',  # FK: assessments, diagnosis_areas
    'assessment_summaries',    # FK: assessments
    'system_config',      # FK: users (opcional)
]


def get_SQLite_connection():
    """Conecta ao SQLite"""
    return sqlite.connector.connect(**SQLite_CONFIG)


def convert_value(value: Any) -> Any:
    """Converte valores SQLite para SQLite/Turso"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value  # BLOB
    if isinstance(value, bool):
        return 1 if value else 0
    # Converter Decimal para float
    from decimal import Decimal
    if isinstance(value, Decimal):
        return float(value)
    return value


async def migrate_table(
    table_name: str,
    SQLite_conn,
    turso_write,
    turso_query,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Migra uma tabela do SQLite para Turso.

    Returns:
        Dict com estatisticas da migracao
    """
    result = {
        'table': table_name,
        'SQLite_count': 0,
        'migrated': 0,
        'skipped': 0,
        'errors': []
    }

    cursor = SQLite_conn.cursor(dictionary=True)

    try:
        # Contar registros no SQLite
        cursor.execute(f"SELECT COUNT(*) as c FROM {table_name}")
        result['SQLite_count'] = cursor.fetchone()['c']

        if result['SQLite_count'] == 0:
            print(f"  {table_name}: 0 registros (vazia)")
            return result

        # Buscar todos os registros
        cursor.execute(f"SELECT * FROM {table_name}")
        rows = cursor.fetchall()

        # Verificar quantos ja existem no Turso
        try:
            turso_count = await turso_query(f"SELECT COUNT(*) as c FROM {table_name}")
            existing = turso_count[0]['c'] if turso_count else 0
        except:
            existing = 0

        if existing > 0:
            print(f"  {table_name}: {existing} registros ja existem no Turso")
            # Decidir: pular ou continuar?
            if existing >= result['SQLite_count']:
                result['skipped'] = existing
                return result

        # Migrar cada registro
        for row in rows:
            # Converter valores
            converted = {k: convert_value(v) for k, v in row.items()}

            # Montar INSERT
            columns = list(converted.keys())
            placeholders = ', '.join(['?' for _ in columns])
            values = [converted[c] for c in columns]

            insert_sql = f"INSERT OR IGNORE INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})"

            if dry_run:
                result['migrated'] += 1
            else:
                try:
                    await turso_write(insert_sql, tuple(values))
                    result['migrated'] += 1
                except Exception as e:
                    error_msg = str(e)
                    # Ignorar erros de UNIQUE constraint (registro ja existe)
                    if 'UNIQUE' in error_msg or 'duplicate' in error_msg.lower():
                        result['skipped'] += 1
                    else:
                        result['errors'].append(error_msg[:100])

        print(f"  {table_name}: {result['migrated']} migrados, {result['skipped']} ignorados")

    except Exception as e:
        result['errors'].append(str(e))
        print(f"  {table_name}: ERRO - {e}")

    finally:
        cursor.close()

    return result


async def main(dry_run: bool = False):
    """Executa a migracao completa"""

    print("=" * 60)
    print("MIGRACAO SQLITE -> TURSO")
    print("=" * 60)

    if dry_run:
        print("\n[DRY RUN] Nenhuma alteracao sera feita\n")

    # Importar funcoes Turso
    from core.turso_database import execute_query, execute_write

    # Conectar SQLite
    print(f"Conectando SQLite: {SQLite_CONFIG['host']}:{SQLite_CONFIG['port']}/{SQLite_CONFIG['database']}")
    SQLite_conn = get_SQLite_connection()
    print("  [OK] SQLite conectado\n")

    # Verificar Turso
    print(f"Conectando Turso: {os.getenv('TURSO_DATABASE_URL')[:50]}...")
    try:
        test = await execute_query("SELECT 1 as ok")
        print("  [OK] Turso conectado\n")
    except Exception as e:
        print(f"  [ERRO] Turso: {e}")
        return

    # Estatisticas
    stats = {
        'total_SQLite': 0,
        'total_migrated': 0,
        'total_skipped': 0,
        'total_errors': 0,
        'tables': []
    }

    # Migrar tabelas na ordem correta
    print("MIGRANDO TABELAS...")
    print("-" * 60)

    for table in MIGRATION_ORDER:
        result = await migrate_table(
            table,
            SQLite_conn,
            execute_write,
            execute_query,
            dry_run
        )

        stats['total_SQLite'] += result['SQLite_count']
        stats['total_migrated'] += result['migrated']
        stats['total_skipped'] += result['skipped']
        stats['total_errors'] += len(result['errors'])
        stats['tables'].append(result)

    SQLite_conn.close()

    # Resumo
    print("\n" + "=" * 60)
    print("RESUMO DA MIGRACAO")
    print("=" * 60)
    print(f"Total no SQLite:    {stats['total_SQLite']}")
    print(f"Migrados:          {stats['total_migrated']}")
    print(f"Ignorados:         {stats['total_skipped']}")
    print(f"Erros:             {stats['total_errors']}")

    # Verificacao final
    print("\n" + "-" * 60)
    print("VERIFICACAO - Contagem no Turso:")
    print("-" * 60)

    for table in MIGRATION_ORDER[:8]:  # Primeiras 8 tabelas
        try:
            count = await execute_query(f"SELECT COUNT(*) as c FROM {table}")
            c = count[0]['c'] if count else 0
            print(f"  {table}: {c}")
        except:
            print(f"  {table}: ERRO")

    print("\n" + "=" * 60)
    print("MIGRACAO CONCLUIDA!")
    print("=" * 60)

    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Migrar SQLite para Turso')
    parser.add_argument('--dry-run', action='store_true', help='Apenas simular, nao migrar')
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run))
