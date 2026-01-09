#!/usr/bin/env python3
"""
Migration 006: Evolution Stages

Sistema de evolução de usuários agnóstico com N estágios configuráveis:
- Lead → Recebe Valor → Troca Valor → Gera Valor
- Cada tenant define seus próprios estágios
- Estágio final pode criar novo tenant (multi-tenant hierárquico)

Alterações:
1. Cria tabela evolution_stages
2. Adiciona colunas ao users (tenant_id, current_stage_key, etc)
3. Adiciona colunas ao tenant_config (parent_tenant_id, owner_user_id)
4. Popula estágios default
5. Migra roles existentes para stages
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

    print("🔧 Migration 006: Evolution Stages")
    print("=" * 60)

    # =====================================================
    # 1. CRIAR TABELA evolution_stages
    # =====================================================
    print("\n📋 Criando tabela evolution_stages...")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS evolution_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT DEFAULT 'default',
            stage_key TEXT NOT NULL,
            stage_name TEXT NOT NULL,
            stage_level INTEGER NOT NULL,
            stage_type TEXT NOT NULL,
            description TEXT,
            creates_tenant BOOLEAN DEFAULT 0,
            permissions TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(tenant_id, stage_key)
        )
    """)
    print("  ✅ Tabela evolution_stages criada")

    # Criar índices
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evolution_stages_tenant ON evolution_stages(tenant_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evolution_stages_level ON evolution_stages(stage_level)")
    print("  ✅ Índices criados")

    # =====================================================
    # 2. ADICIONAR COLUNAS AO users
    # =====================================================
    print("\n👤 Adicionando colunas ao users...")

    user_columns = [
        ("tenant_id", "TEXT DEFAULT 'default'"),
        ("current_stage_key", "TEXT DEFAULT 'lead'"),
        ("stage_history", "TEXT"),  # JSON array
        ("promoted_to_tenant_id", "TEXT"),
    ]

    cursor = conn.execute("PRAGMA table_info(users)")
    existing_columns = {row['name'] for row in cursor.fetchall()}

    for col_name, col_def in user_columns:
        if col_name not in existing_columns:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
                print(f"  ✅ Coluna 'users.{col_name}' adicionada")
            except sqlite3.OperationalError as e:
                print(f"  ⚠️ Coluna 'users.{col_name}': {e}")
        else:
            print(f"  ⏭️ Coluna 'users.{col_name}' já existe")

    # =====================================================
    # 3. ADICIONAR COLUNAS AO tenant_config
    # =====================================================
    print("\n🏢 Adicionando colunas ao tenant_config...")

    tenant_columns = [
        ("parent_tenant_id", "TEXT"),
        ("owner_user_id", "INTEGER"),
        ("tenant_type", "TEXT DEFAULT 'root'"),
    ]

    cursor = conn.execute("PRAGMA table_info(tenant_config)")
    existing_columns = {row['name'] for row in cursor.fetchall()}

    for col_name, col_def in tenant_columns:
        if col_name not in existing_columns:
            try:
                conn.execute(f"ALTER TABLE tenant_config ADD COLUMN {col_name} {col_def}")
                print(f"  ✅ Coluna 'tenant_config.{col_name}' adicionada")
            except sqlite3.OperationalError as e:
                print(f"  ⚠️ Coluna 'tenant_config.{col_name}': {e}")
        else:
            print(f"  ⏭️ Coluna 'tenant_config.{col_name}' já existe")

    # =====================================================
    # 4. POPULAR ESTÁGIOS DEFAULT
    # =====================================================
    print("\n🎯 Populando estágios default...")

    default_stages = [
        {
            "stage_key": "lead",
            "stage_name": "Lead",
            "stage_level": 0,
            "stage_type": "lead",
            "description": "Novo contato que ainda não recebeu valor",
            "creates_tenant": False,
            "permissions": json.dumps(["view_public"])
        },
        {
            "stage_key": "receives_value",
            "stage_name": "Mentorado",
            "stage_level": 1,
            "stage_type": "receives_value",
            "description": "Recebe valor através de mentoria, conteúdo ou serviços",
            "creates_tenant": False,
            "permissions": json.dumps(["view_public", "chat", "diagnosis"])
        },
        {
            "stage_key": "trades_value",
            "stage_name": "Cliente Ativo",
            "stage_level": 2,
            "stage_type": "trades_value",
            "description": "Troca valor - cliente pagante ou contribuidor ativo",
            "creates_tenant": False,
            "permissions": json.dumps(["view_public", "chat", "diagnosis", "reports"])
        },
        {
            "stage_key": "generates_value",
            "stage_name": "Mentor",
            "stage_level": 3,
            "stage_type": "generates_value",
            "description": "Gera valor para outros - mentor, afiliado ou multiplicador",
            "creates_tenant": True,
            "permissions": json.dumps(["admin"])
        },
    ]

    for stage in default_stages:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO evolution_stages
                (tenant_id, stage_key, stage_name, stage_level, stage_type,
                 description, creates_tenant, permissions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "default",
                stage["stage_key"],
                stage["stage_name"],
                stage["stage_level"],
                stage["stage_type"],
                stage["description"],
                stage["creates_tenant"],
                stage["permissions"]
            ))
            print(f"  ✅ Estágio '{stage['stage_key']}' ({stage['stage_name']})")
        except Exception as e:
            print(f"  ⚠️ Estágio '{stage['stage_key']}': {e}")

    # =====================================================
    # 5. MIGRAR ROLES EXISTENTES PARA STAGES
    # =====================================================
    print("\n🔄 Migrando roles existentes para stages...")

    # Mapear roles para stage_keys
    role_to_stage = {
        "lead": "lead",
        "mentorado": "receives_value",
        "mentor": "generates_value",
        "admin": "generates_value",
    }

    for role, stage_key in role_to_stage.items():
        result = conn.execute("""
            UPDATE users
            SET current_stage_key = ?, tenant_id = 'default'
            WHERE role = ? AND (current_stage_key IS NULL OR current_stage_key = 'lead')
        """, (stage_key, role))
        print(f"  ✅ Role '{role}' → Stage '{stage_key}': {result.rowcount} usuários")

    # =====================================================
    # 6. CRIAR ÍNDICES ADICIONAIS
    # =====================================================
    print("\n📊 Criando índices adicionais...")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_stage ON users(current_stage_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tenant_parent ON tenant_config(parent_tenant_id)")
    print("  ✅ Índices criados")

    # =====================================================
    # 7. COMMIT
    # =====================================================
    conn.commit()
    conn.close()

    print("\n" + "=" * 60)
    print("✅ Migration 006 concluída com sucesso!")
    print("\nResumo:")
    print("  - Tabela evolution_stages criada")
    print("  - 4 colunas adicionadas ao users")
    print("  - 3 colunas adicionadas ao tenant_config")
    print("  - 4 estágios default populados")
    print("  - Roles existentes migrados para stages")
    print("\nEstágios do Flywheel:")
    print("  0. Lead (lead)")
    print("  1. Mentorado (receives_value)")
    print("  2. Cliente Ativo (trades_value)")
    print("  3. Mentor (generates_value) → cria tenant")


def rollback():
    """Reverte a migração."""
    conn = sqlite3.connect(DB_PATH)

    print("🔙 Rollback Migration 006...")

    # Dropar tabela
    conn.execute("DROP TABLE IF EXISTS evolution_stages")
    print("  ✅ Tabela evolution_stages removida")

    # Nota: SQLite não suporta DROP COLUMN facilmente
    # As colunas adicionadas ficarão no banco

    conn.commit()
    conn.close()
    print("✅ Rollback concluído (colunas mantidas)")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        rollback()
    else:
        run_migration()
