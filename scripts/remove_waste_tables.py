#!/usr/bin/env python3
"""
Script de Remocao de Tabelas de Residuos

Remove as tabelas relacionadas ao sistema de residuos ambientais:
- mentor_invites
- analysis_results
- reports
- hotspots

Uso:
    cd claude_backend_sdk_hibrid
    source venv/bin/activate
    python scripts/remove_waste_tables.py --dry-run  # Simular
    python scripts/remove_waste_tables.py            # Executar
"""

import asyncio
import os
import sys
from typing import List, Dict, Any

# Adicionar path do projeto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Carregar .env
load_dotenv()

# Tabelas a serem removidas (ordem importa - remover filhas antes das pais)
TABLES_TO_REMOVE = [
    'analysis_results',  # FK: reports
    'reports',           # FK: users
    'hotspots',          # Independente
    'mentor_invites',    # FK: users
]


async def backup_table(table_name: str, turso_query) -> Dict[str, Any]:
    """
    Faz backup dos dados de uma tabela antes de remover.

    Returns:
        Dict com os dados e metadados
    """
    try:
        # Buscar todos os dados
        data = await turso_query(f"SELECT * FROM {table_name}")

        # Buscar estrutura da tabela
        schema = await turso_query(f"PRAGMA table_info({table_name})")

        return {
            'table': table_name,
            'row_count': len(data),
            'data': data,
            'schema': schema,
            'success': True
        }
    except Exception as e:
        return {
            'table': table_name,
            'error': str(e),
            'success': False
        }


async def drop_table(table_name: str, turso_write, dry_run: bool = False) -> Dict[str, Any]:
    """
    Remove uma tabela do banco.

    Returns:
        Dict com resultado da operacao
    """
    result = {
        'table': table_name,
        'dropped': False,
        'error': None
    }

    try:
        if dry_run:
            print(f"  [DRY RUN] DROP TABLE IF EXISTS {table_name}")
            result['dropped'] = True
        else:
            await turso_write(f"DROP TABLE IF EXISTS {table_name}")
            result['dropped'] = True
            print(f"  ✅ Tabela '{table_name}' removida")
    except Exception as e:
        result['error'] = str(e)
        print(f"  ❌ Erro ao remover '{table_name}': {e}")

    return result


async def verify_tables_exist(turso_query) -> List[str]:
    """Verifica quais tabelas ainda existem"""
    try:
        tables = await turso_query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [t['name'] for t in tables]
    except Exception as e:
        print(f"Erro ao verificar tabelas: {e}")
        return []


async def main(dry_run: bool = False, backup: bool = True):
    """Executa a remocao das tabelas"""

    print("=" * 60)
    print("REMOCAO DE TABELAS - SISTEMA DE RESIDUOS")
    print("=" * 60)

    if dry_run:
        print("\n[DRY RUN] Nenhuma alteracao sera feita\n")

    # Importar funcoes Turso
    from core.turso_database import db

    # Verificar conexao
    print(f"Conectando Turso: {os.getenv('TURSO_DATABASE_URL', '')[:50]}...")
    health = await db.health_check()

    if health['status'] != 'healthy':
        print(f"  ❌ Erro: {health}")
        return

    print("  ✅ Turso conectado\n")

    # Listar tabelas existentes
    print("TABELAS EXISTENTES:")
    print("-" * 60)
    existing_tables = await verify_tables_exist(db.query_async)
    for table in existing_tables:
        marker = "🗑️ " if table in TABLES_TO_REMOVE else "  "
        print(f"{marker}{table}")
    print()

    # Fazer backup antes de remover
    backups = {}
    if backup and not dry_run:
        print("FAZENDO BACKUP DOS DADOS:")
        print("-" * 60)

        for table in TABLES_TO_REMOVE:
            if table in existing_tables:
                print(f"  Backup de '{table}'...", end=" ")
                backup_result = await backup_table(table, db.query_async)
                backups[table] = backup_result

                if backup_result['success']:
                    print(f"✅ {backup_result['row_count']} registros")
                else:
                    print(f"❌ Erro: {backup_result.get('error')}")
        print()

    # Remover tabelas
    print("REMOVENDO TABELAS:")
    print("-" * 60)

    results = []
    for table in TABLES_TO_REMOVE:
        if table in existing_tables or dry_run:
            result = await drop_table(table, db.execute_async, dry_run)
            results.append(result)
        else:
            print(f"  ⏭️  Tabela '{table}' nao existe (pulando)")

    print()

    # Verificacao final
    if not dry_run:
        print("VERIFICACAO FINAL:")
        print("-" * 60)
        remaining_tables = await verify_tables_exist(db.query_async)

        print("Tabelas restantes:")
        for table in remaining_tables:
            print(f"  ✓ {table}")

        print("\nTabelas removidas:")
        for table in TABLES_TO_REMOVE:
            if table not in remaining_tables:
                print(f"  ✓ {table}")

    # Resumo
    print("\n" + "=" * 60)
    print("RESUMO")
    print("=" * 60)

    dropped_count = sum(1 for r in results if r['dropped'])
    error_count = sum(1 for r in results if r['error'])

    print(f"Tabelas processadas: {len(results)}")
    print(f"Removidas:           {dropped_count}")
    print(f"Erros:               {error_count}")

    if backups:
        total_backed_up = sum(b['row_count'] for b in backups.values() if b['success'])
        print(f"Registros em backup: {total_backed_up}")

    print("\n" + "=" * 60)

    if dry_run:
        print("DRY RUN CONCLUIDO - Nenhuma alteracao foi feita")
    else:
        print("REMOCAO CONCLUIDA!")

    print("=" * 60)

    return {
        'results': results,
        'backups': backups,
        'dry_run': dry_run
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Remover tabelas de residuos do banco')
    parser.add_argument('--dry-run', action='store_true', help='Apenas simular, nao remover')
    parser.add_argument('--no-backup', action='store_true', help='Nao fazer backup antes de remover')
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run, backup=not args.no_backup))
