#!/usr/bin/env python3
"""
Script para executar migrations no banco de dados.

Uso:
    python migrations/run_migrations.py
"""

import os
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

# Adicionar claude_backend_sdk_hibrid ao path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_db_path() -> str:
    """Retorna o caminho do banco de dados."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'crm.db')


def get_migration_files() -> list:
    """Lista todos os arquivos de migration ordenados."""
    migrations_dir = Path(__file__).parent
    sql_files = sorted(migrations_dir.glob('*.sql'))
    return sql_files


def get_executed_migrations(conn: sqlite3.Connection) -> set:
    """Retorna conjunto de migrations ja executadas."""
    try:
        cursor = conn.execute("""
            SELECT migration_name FROM _migrations
        """)
        return {row[0] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        # Tabela nao existe ainda
        return set()


def create_migrations_table(conn: sqlite3.Connection):
    """Cria tabela de controle de migrations."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            migration_name TEXT UNIQUE NOT NULL,
            executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def run_migration(conn: sqlite3.Connection, migration_file: Path):
    """Executa uma migration."""
    migration_name = migration_file.name
    print(f"  Executando: {migration_name}")

    # Ler SQL
    sql = migration_file.read_text()

    # Executar SQL (pode ter multiplos statements)
    try:
        conn.executescript(sql)

        # Registrar migration executada
        conn.execute(
            "INSERT INTO _migrations (migration_name) VALUES (?)",
            (migration_name,)
        )
        conn.commit()
        print(f"  [OK] {migration_name}")
        return True
    except Exception as e:
        print(f"  [ERRO] {migration_name}: {e}")
        conn.rollback()
        return False


def main():
    """Executa todas as migrations pendentes."""
    print("=" * 50)
    print("Migration Runner")
    print("=" * 50)

    db_path = get_db_path()
    print(f"Banco de dados: {db_path}")

    if not os.path.exists(db_path):
        print("ERRO: Banco de dados nao encontrado!")
        sys.exit(1)

    # Conectar
    conn = sqlite3.connect(db_path)
    print("Conectado ao banco.")

    # Criar tabela de controle
    create_migrations_table(conn)

    # Obter migrations ja executadas
    executed = get_executed_migrations(conn)
    print(f"Migrations ja executadas: {len(executed)}")

    # Obter arquivos de migration
    migration_files = get_migration_files()
    print(f"Arquivos de migration encontrados: {len(migration_files)}")

    # Executar migrations pendentes
    pending = [f for f in migration_files if f.name not in executed]
    print(f"Migrations pendentes: {len(pending)}")

    if not pending:
        print("\nNenhuma migration pendente.")
        conn.close()
        return

    print("\nExecutando migrations:")
    success = 0
    failed = 0

    for migration_file in pending:
        if run_migration(conn, migration_file):
            success += 1
        else:
            failed += 1

    print("\n" + "=" * 50)
    print(f"Resultado: {success} sucesso, {failed} falhas")
    print("=" * 50)

    conn.close()

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
