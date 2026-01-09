#!/usr/bin/env python3
"""
Migration 007: Admin Levels (Níveis Hierárquicos de Gestão)

Sistema de níveis de gestão agnóstico:
- Nível 1 = Dono Master (controle total)
- Nível 2, 3, 4... = Níveis configuráveis de gestão
- Separado do Flywheel (que é para jornada do cliente)

Alterações:
1. Cria tabela admin_levels
2. Adiciona coluna admin_level ao users
3. Popula níveis default
4. Atribui nível 1 ao admin existente
"""

import os
import sqlite3
import json
from datetime import datetime

# Path do banco
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'crm.db')


def run_migration():
    """Executa a migração."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print("🔧 Migration 007: Admin Levels")
    print("=" * 60)

    # =====================================================
    # 1. CRIAR TABELA admin_levels
    # =====================================================
    print("\n📋 Criando tabela admin_levels...")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT DEFAULT 'default',
            level INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            permissions TEXT,
            can_manage_levels TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(tenant_id, level)
        )
    """)
    print("  ✅ Tabela admin_levels criada")

    # Criar índices
    conn.execute("CREATE INDEX IF NOT EXISTS idx_admin_levels_tenant ON admin_levels(tenant_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_admin_levels_level ON admin_levels(level)")
    print("  ✅ Índices criados")

    # =====================================================
    # 2. ADICIONAR COLUNA admin_level AO users
    # =====================================================
    print("\n👤 Adicionando coluna admin_level ao users...")

    cursor = conn.execute("PRAGMA table_info(users)")
    existing_columns = {row['name'] for row in cursor.fetchall()}

    if 'admin_level' not in existing_columns:
        try:
            conn.execute("ALTER TABLE users ADD COLUMN admin_level INTEGER DEFAULT NULL")
            print("  ✅ Coluna 'users.admin_level' adicionada")
        except sqlite3.OperationalError as e:
            print(f"  ⚠️ Coluna 'users.admin_level': {e}")
    else:
        print("  ⏭️ Coluna 'users.admin_level' já existe")

    # Criar índice
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_admin_level ON users(admin_level)")
    print("  ✅ Índice criado")

    # =====================================================
    # 3. POPULAR NÍVEIS DEFAULT
    # =====================================================
    print("\n🎯 Populando níveis default...")

    default_levels = [
        {
            "level": 0,
            "name": "Dono",
            "description": "Controle total do sistema",
            "permissions": json.dumps(["*"]),
            "can_manage_levels": "1,2,3,4,5"
        },
        {
            "level": 1,
            "name": "Diretor",
            "description": "Gerencia gestores e visualiza métricas gerais",
            "permissions": json.dumps(["view_all", "manage_users", "reports"]),
            "can_manage_levels": "2,3,4,5"
        },
        {
            "level": 2,
            "name": "Gestor",
            "description": "Gerencia clientes e mentorados diretamente",
            "permissions": json.dumps(["view_team", "manage_clients"]),
            "can_manage_levels": "3,4,5"
        },
    ]

    for level_data in default_levels:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO admin_levels
                (tenant_id, level, name, description, permissions, can_manage_levels)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                "default",
                level_data["level"],
                level_data["name"],
                level_data["description"],
                level_data["permissions"],
                level_data["can_manage_levels"]
            ))
            print(f"  ✅ Nível {level_data['level']}: {level_data['name']}")
        except Exception as e:
            print(f"  ⚠️ Nível {level_data['level']}: {e}")

    # =====================================================
    # 4. ATRIBUIR NÍVEL 0 AOS ADMINS EXISTENTES
    # =====================================================
    print("\n🔄 Atribuindo nível 0 aos admins existentes...")

    result = conn.execute("""
        UPDATE users
        SET admin_level = 0
        WHERE role = 'admin' AND admin_level IS NULL
    """)
    print(f"  ✅ {result.rowcount} usuário(s) promovido(s) a Nível 0 (Dono)")

    # =====================================================
    # 5. COMMIT
    # =====================================================
    conn.commit()
    conn.close()

    print("\n" + "=" * 60)
    print("✅ Migration 007 concluída com sucesso!")
    print("\nResumo:")
    print("  - Tabela admin_levels criada")
    print("  - Coluna admin_level adicionada ao users")
    print("  - 3 níveis default populados")
    print("  - Admins existentes promovidos a Nível 0")
    print("\nNíveis Hierárquicos:")
    print("  0. Dono (controle total)")
    print("  1. Diretor (gerencia gestores)")
    print("  2. Gestor (gerencia clientes)")


def rollback():
    """Reverte a migração."""
    conn = sqlite3.connect(DB_PATH)

    print("🔙 Rollback Migration 007...")

    # Dropar tabela
    conn.execute("DROP TABLE IF EXISTS admin_levels")
    print("  ✅ Tabela admin_levels removida")

    # Nota: SQLite não suporta DROP COLUMN facilmente
    # A coluna admin_level ficará no banco

    conn.commit()
    conn.close()
    print("✅ Rollback concluído (coluna mantida)")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        rollback()
    else:
        run_migration()
