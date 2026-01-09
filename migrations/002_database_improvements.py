#!/usr/bin/env python3
"""
Migration 002: Database Improvements
- Adiciona índices nas colunas mais consultadas
- Padroniza timestamps (created_at/updated_at)
- Adiciona soft delete (deleted_at)
- Habilita foreign keys enforcement

Nota: SQLite não suporta ALTER TABLE ADD CONSTRAINT para FKs,
então as FKs só são enforced em novas tabelas criadas com a constraint.
"""

import libsql_experimental as libsql
from datetime import datetime


def run_migration():
    conn = libsql.connect('/home/diagnostico/.turso/databases/crm.db')

    print("🔧 Migration 002: Database Improvements")
    print("=" * 50)

    # Habilitar foreign keys (importante para SQLite)
    conn.execute("PRAGMA foreign_keys = ON")
    print("✅ Foreign keys enforcement habilitado")

    # =====================================================
    # 1. ÍNDICES ADICIONAIS
    # =====================================================
    print("\n📊 Criando índices adicionais...")

    indices = [
        # chat_sessions
        ("idx_chat_sessions_user", "chat_sessions", "user_id"),
        ("idx_chat_sessions_created", "chat_sessions", "created_at"),
        ("idx_chat_sessions_updated", "chat_sessions", "updated_at"),

        # chat_messages
        ("idx_chat_messages_session", "chat_messages", "session_id"),
        ("idx_chat_messages_user", "chat_messages", "user_id"),
        ("idx_chat_messages_created", "chat_messages", "created_at"),

        # assessments
        ("idx_assessments_user", "assessments", "user_id"),
        ("idx_assessments_status", "assessments", "status"),
        ("idx_assessments_started", "assessments", "started_at"),

        # assessment_answers
        ("idx_assessment_answers_assessment", "assessment_answers", "assessment_id"),

        # assessment_area_scores
        ("idx_assessment_area_scores_assessment", "assessment_area_scores", "assessment_id"),

        # assessment_summaries
        ("idx_assessment_summaries_assessment", "assessment_summaries", "assessment_id"),

        # crm_lead_state - adicionar estado
        ("idx_crm_lead_state_state", "crm_lead_state", "current_state"),
        ("idx_crm_lead_state_owner", "crm_lead_state", "owner_team"),

        # users - role e status
        ("idx_users_role", "users", "role"),
        ("idx_users_status", "users", "account_status"),

        # refresh_tokens
        ("idx_refresh_tokens_user", "refresh_tokens", "user_id"),
        ("idx_refresh_tokens_expires", "refresh_tokens", "expires_at"),
    ]

    for idx_name, table, column in indices:
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({column})")
            print(f"  ✅ {idx_name}")
        except Exception as e:
            if "already exists" in str(e).lower():
                print(f"  ⏭️  {idx_name} (já existe)")
            else:
                print(f"  ❌ {idx_name}: {e}")

    conn.commit()

    # =====================================================
    # 2. PADRONIZAR TIMESTAMPS - Adicionar created_at/updated_at onde falta
    # =====================================================
    print("\n⏰ Padronizando timestamps...")

    # Tabelas que precisam de created_at/updated_at
    tables_need_timestamps = [
        "crm_lead_state",
        "crm_products",
    ]

    for table in tables_need_timestamps:
        try:
            # Verificar se coluna existe
            cursor = conn.execute(f"PRAGMA table_info({table})")
            columns = [col[1] for col in cursor.fetchall()]

            if "created_at" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN created_at TEXT DEFAULT (datetime('now'))")
                print(f"  ✅ {table}.created_at adicionado")

            if "updated_at" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT DEFAULT (datetime('now'))")
                print(f"  ✅ {table}.updated_at adicionado")

        except Exception as e:
            print(f"  ❌ {table}: {e}")

    conn.commit()

    # =====================================================
    # 3. SOFT DELETE - Adicionar deleted_at
    # =====================================================
    print("\n🗑️  Adicionando soft delete (deleted_at)...")

    tables_need_soft_delete = [
        "users",
        "chat_sessions",
        "assessments",
        "crm_lead_state",
        "crm_orders",
        "crm_meetings",
        "crm_tasks",
        "crm_followups",
    ]

    for table in tables_need_soft_delete:
        try:
            cursor = conn.execute(f"PRAGMA table_info({table})")
            columns = [col[1] for col in cursor.fetchall()]

            if "deleted_at" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TEXT DEFAULT NULL")
                print(f"  ✅ {table}.deleted_at adicionado")
            else:
                print(f"  ⏭️  {table}.deleted_at (já existe)")

        except Exception as e:
            print(f"  ❌ {table}: {e}")

    conn.commit()

    # =====================================================
    # 4. CRIAR VIEWS ÚTEIS
    # =====================================================
    print("\n👁️  Criando views úteis...")

    # View de leads ativos (não deletados)
    try:
        conn.execute("""
            CREATE VIEW IF NOT EXISTS vw_active_leads AS
            SELECT
                u.user_id as lead_id,
                u.username as nome,
                u.email,
                u.phone_number as telefone,
                u.profession as profissao,
                u.registration_date as created_at,
                ls.current_state,
                ls.owner_team,
                ls.state_updated_at,
                ls.notes
            FROM users u
            LEFT JOIN crm_lead_state ls ON u.user_id = ls.lead_id
            WHERE (u.role = 'lead' OR u.account_status = 'lead')
            AND u.deleted_at IS NULL
            AND (ls.deleted_at IS NULL OR ls.lead_id IS NULL)
        """)
        print("  ✅ vw_active_leads")
    except Exception as e:
        print(f"  ❌ vw_active_leads: {e}")

    # View de funil CRM
    try:
        conn.execute("""
            CREATE VIEW IF NOT EXISTS vw_crm_funnel AS
            SELECT
                current_state,
                COUNT(*) as total,
                owner_team
            FROM crm_lead_state
            WHERE deleted_at IS NULL
            GROUP BY current_state, owner_team
            ORDER BY
                CASE current_state
                    WHEN 'novo' THEN 1
                    WHEN 'diagnostico_pendente' THEN 2
                    WHEN 'diagnostico_agendado' THEN 3
                    WHEN 'em_atendimento' THEN 4
                    WHEN 'proposta_enviada' THEN 5
                    WHEN 'negociacao' THEN 6
                    WHEN 'produto_vendido' THEN 7
                    WHEN 'followup_ativo' THEN 8
                    WHEN 'perdido' THEN 9
                    ELSE 10
                END
        """)
        print("  ✅ vw_crm_funnel")
    except Exception as e:
        print(f"  ❌ vw_crm_funnel: {e}")

    # View de mentorados ativos
    try:
        conn.execute("""
            CREATE VIEW IF NOT EXISTS vw_active_mentorados AS
            SELECT
                user_id,
                username,
                email,
                phone_number,
                profession,
                specialty,
                current_revenue,
                desired_revenue,
                registration_date as created_at
            FROM users
            WHERE role = 'mentorado'
            AND account_status = 'active'
            AND deleted_at IS NULL
        """)
        print("  ✅ vw_active_mentorados")
    except Exception as e:
        print(f"  ❌ vw_active_mentorados: {e}")

    conn.commit()

    # =====================================================
    # 5. ANÁLISE DAS TABELAS fs_*
    # =====================================================
    print("\n📁 Análise das tabelas fs_* (filesystem)...")

    fs_tables = ['fs_config', 'fs_data', 'fs_dentry', 'fs_inode', 'fs_symlink']
    for table in fs_tables:
        cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"  {table}: {count} registros")

    print("\n  💡 Recomendação: As tabelas fs_* são do AgentFS (filesystem virtual).")
    print("     Se não estiver usando, pode remover futuramente.")
    print("     Por enquanto, mantendo para compatibilidade.")

    # =====================================================
    # 6. REGISTRAR MIGRAÇÃO
    # =====================================================
    print("\n📝 Registrando migração...")

    try:
        conn.execute("""
            INSERT INTO _migrations (name, applied_at)
            VALUES ('002_database_improvements', datetime('now'))
        """)
        conn.commit()
        print("  ✅ Migração 002 registrada")
    except Exception as e:
        print(f"  ⚠️  Não foi possível registrar: {e}")

    # =====================================================
    # RESUMO FINAL
    # =====================================================
    print("\n" + "=" * 50)
    print("✅ Migration 002 concluída!")
    print("=" * 50)

    # Verificar índices totais
    cursor = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
    total_indices = cursor.fetchone()[0]
    print(f"\n📊 Total de índices: {total_indices}")

    # Verificar views
    cursor = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'")
    total_views = cursor.fetchone()[0]
    print(f"👁️  Total de views: {total_views}")

    conn.close()


if __name__ == "__main__":
    run_migration()
