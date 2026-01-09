#!/usr/bin/env python3
"""
Migration 004: Tenant Configuration (White Label)

Cria tabelas para configuração White Label:
- tenant_config: Identidade visual e features
- diagnosis_areas: Áreas de diagnóstico configuráveis
- agent_configs: Prompts de agentes editáveis
- crm_config: Configuração do CRM (opcional)

Popula com dados atuais como seed inicial.
"""

import os
import sys
import sqlite3
from datetime import datetime

# Path do banco
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'crm.db')


def run_migration():
    """Executa a migração."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print("🔧 Migration 004: Tenant Configuration (White Label)")
    print("=" * 60)

    # =====================================================
    # 1. TABELA tenant_config
    # =====================================================
    print("\n📋 Criando tabela tenant_config...")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tenant_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT UNIQUE DEFAULT 'default',

            -- Identidade
            brand_name TEXT NOT NULL DEFAULT 'Nanda',
            brand_tagline TEXT DEFAULT 'sua mentora',
            brand_description TEXT DEFAULT 'Sistema de Diagnóstico Inteligente',

            -- Cores (hex)
            primary_color TEXT DEFAULT '#059669',
            primary_light TEXT DEFAULT '#d1fae5',
            primary_dark TEXT DEFAULT '#047857',
            secondary_color TEXT DEFAULT '#10b981',

            -- Assets
            logo_url TEXT,
            favicon_url TEXT,

            -- Domínios
            api_domain TEXT DEFAULT 'localhost:8234',
            web_domain TEXT DEFAULT 'localhost:4200',

            -- Features (0/1 para SQLite)
            crm_enabled INTEGER DEFAULT 1,
            diagnosis_enabled INTEGER DEFAULT 1,
            chat_enabled INTEGER DEFAULT 1,

            -- Metadados
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    print("  ✅ Tabela tenant_config criada")

    # =====================================================
    # 2. TABELA diagnosis_areas
    # =====================================================
    print("\n📊 Criando tabela diagnosis_areas...")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS diagnosis_areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT DEFAULT 'default',
            area_key TEXT NOT NULL,
            area_name TEXT NOT NULL,
            area_description TEXT,
            area_icon TEXT DEFAULT '📊',
            display_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(tenant_id, area_key)
        )
    """)
    print("  ✅ Tabela diagnosis_areas criada")

    # =====================================================
    # 3. TABELA agent_configs
    # =====================================================
    print("\n🤖 Criando tabela agent_configs...")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT DEFAULT 'default',
            agent_key TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            agent_description TEXT,
            system_prompt TEXT NOT NULL,
            model TEXT DEFAULT 'claude-sonnet-4-20250514',
            temperature REAL DEFAULT 0.7,
            max_tokens INTEGER DEFAULT 4096,
            allowed_tools TEXT,
            allowed_roles TEXT DEFAULT '["admin"]',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(tenant_id, agent_key)
        )
    """)
    print("  ✅ Tabela agent_configs criada")

    # =====================================================
    # 4. TABELA crm_config
    # =====================================================
    print("\n🎯 Criando tabela crm_config...")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS crm_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT UNIQUE DEFAULT 'default',
            lead_states TEXT,
            scoring_model TEXT,
            sla_config TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    print("  ✅ Tabela crm_config criada")

    conn.commit()

    # =====================================================
    # 5. ÍNDICES
    # =====================================================
    print("\n📈 Criando índices...")

    indices = [
        ("idx_diagnosis_areas_tenant", "diagnosis_areas", "tenant_id"),
        ("idx_diagnosis_areas_active", "diagnosis_areas", "is_active"),
        ("idx_agent_configs_tenant", "agent_configs", "tenant_id"),
        ("idx_agent_configs_active", "agent_configs", "is_active"),
    ]

    for idx_name, table, column in indices:
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({column})")
            print(f"  ✅ {idx_name}")
        except Exception as e:
            print(f"  ⚠️  {idx_name}: {e}")

    conn.commit()

    # =====================================================
    # 6. SEED: tenant_config default
    # =====================================================
    print("\n🌱 Populando tenant_config...")

    try:
        conn.execute("""
            INSERT OR IGNORE INTO tenant_config (
                tenant_id, brand_name, brand_tagline, brand_description,
                primary_color, primary_light, primary_dark, secondary_color,
                api_domain, web_domain, crm_enabled, diagnosis_enabled, chat_enabled
            ) VALUES (
                'default', 'Nanda', 'sua mentora', 'Sistema de Diagnóstico Inteligente para Profissionais de Saúde',
                '#059669', '#d1fae5', '#047857', '#10b981',
                'crm-api.nandamac.cloud', 'mvp.nandamac.cloud', 1, 1, 1
            )
        """)
        print("  ✅ Config default criada")
    except Exception as e:
        print(f"  ⚠️  Config default: {e}")

    conn.commit()

    # =====================================================
    # 7. SEED: diagnosis_areas (7 áreas atuais)
    # =====================================================
    print("\n🌱 Populando diagnosis_areas...")

    areas = [
        ("mindset_high_ticket", "Mentalidade High Ticket", "Avalia a mentalidade do profissional para vendas de alto valor", "🧠", 1),
        ("patient_treatment", "Paciente e Plano de Tratamento", "Analisa como o profissional estrutura tratamentos", "👤", 2),
        ("journey_experience", "Jornada e Encantamento", "Avalia a experiência do paciente na clínica", "✨", 3),
        ("attraction_retention", "Atração e Fidelização", "Analisa estratégias de marketing e retenção", "🎯", 4),
        ("team_management", "Secretárias e Auxiliares", "Avalia gestão de equipe e delegação", "👥", 5),
        ("business_management", "Seu Negócio", "Analisa aspectos financeiros e operacionais", "💼", 6),
        ("technical_domain", "Domínio Técnico", "Avalia conhecimento técnico e atualizações", "🔬", 7),
    ]

    for area_key, area_name, area_desc, icon, order in areas:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO diagnosis_areas (
                    tenant_id, area_key, area_name, area_description, area_icon, display_order
                ) VALUES ('default', ?, ?, ?, ?, ?)
            """, (area_key, area_name, area_desc, icon, order))
            print(f"  ✅ {area_name}")
        except Exception as e:
            print(f"  ⚠️  {area_name}: {e}")

    conn.commit()

    # =====================================================
    # 8. SEED: agent_configs (agentes principais)
    # =====================================================
    print("\n🌱 Populando agent_configs...")

    agents = [
        {
            "key": "diagnostic-expert",
            "name": "Especialista em Diagnóstico",
            "description": "Conduz diagnósticos estruturados nas áreas configuradas",
            "prompt": """Você é {brand_name}, especialista em diagnóstico de profissionais.

Sua função é conduzir um diagnóstico estruturado avaliando as seguintes áreas:
{diagnosis_areas}

Para cada área, avalie de 0 a 100 e forneça:
- Pontos fortes identificados
- Áreas de melhoria
- Recomendações específicas

Use a ferramenta save_diagnosis para salvar o resultado.""",
            "roles": '["admin", "mentorado"]',
            "tools": '["mcp__platform__save_diagnosis", "mcp__platform__get_diagnosis_areas"]'
        },
        {
            "key": "admin-assistant",
            "name": "Assistente Administrativo",
            "description": "Assistente para administradores com acesso ao banco de dados",
            "prompt": """Você é {brand_name}, assistente administrativa com acesso ao banco de dados do sistema.

Você pode:
- Consultar dados de usuários, sessões e diagnósticos
- Gerar relatórios e estatísticas
- Ajudar com tarefas administrativas

Seja sempre clara e objetiva nas respostas. Use as ferramentas disponíveis para buscar informações precisas.""",
            "roles": '["admin"]',
            "tools": '["mcp__platform__execute_sql_query", "mcp__platform__get_user_diagnosis", "mcp__platform__get_user_chat_sessions"]'
        },
        {
            "key": "business-consultant",
            "name": "Consultor de Negócios",
            "description": "Consultor especializado em crescimento de negócios",
            "prompt": """Você é {brand_name}, consultor especializado em ajudar profissionais a crescerem seus negócios.

Seu foco é:
- Estratégias de posicionamento e diferenciação
- Precificação e proposta de valor
- Marketing e captação de clientes
- Gestão financeira e operacional

Forneça orientações práticas e acionáveis baseadas no perfil e diagnóstico do profissional.""",
            "roles": '["admin", "mentorado"]',
            "tools": '["mcp__platform__get_user_diagnosis"]'
        },
    ]

    for agent in agents:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO agent_configs (
                    tenant_id, agent_key, agent_name, agent_description,
                    system_prompt, allowed_roles, allowed_tools
                ) VALUES ('default', ?, ?, ?, ?, ?, ?)
            """, (
                agent["key"], agent["name"], agent["description"],
                agent["prompt"], agent["roles"], agent["tools"]
            ))
            print(f"  ✅ {agent['name']}")
        except Exception as e:
            print(f"  ⚠️  {agent['name']}: {e}")

    conn.commit()

    # =====================================================
    # 9. SEED: crm_config
    # =====================================================
    print("\n🌱 Populando crm_config...")

    import json

    lead_states = json.dumps([
        "novo", "diagnostico_pendente", "diagnostico_agendado",
        "em_atendimento", "proposta_enviada", "negociacao",
        "produto_vendido", "followup_ativo", "perdido", "reativacao"
    ])

    scoring_model = json.dumps({
        "perfil_peso": 40,
        "engajamento_peso": 30,
        "timing_peso": 30,
        "temperaturas": {
            "quente": {"min": 70, "max": 100},
            "morno": {"min": 40, "max": 69},
            "frio": {"min": 0, "max": 39}
        }
    })

    sla_config = json.dumps({
        "novo_para_contato": "24h",
        "diagnostico_pendente": "48h",
        "proposta_followup": "72h"
    })

    try:
        conn.execute("""
            INSERT OR IGNORE INTO crm_config (
                tenant_id, lead_states, scoring_model, sla_config, is_active
            ) VALUES ('default', ?, ?, ?, 1)
        """, (lead_states, scoring_model, sla_config))
        print("  ✅ CRM config criada")
    except Exception as e:
        print(f"  ⚠️  CRM config: {e}")

    conn.commit()

    # =====================================================
    # 10. REGISTRAR MIGRAÇÃO
    # =====================================================
    print("\n📝 Registrando migração...")

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                applied_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO _migrations (name, applied_at)
            VALUES ('004_tenant_config', datetime('now'))
        """)
        conn.commit()
        print("  ✅ Migração 004 registrada")
    except Exception as e:
        print(f"  ⚠️  Registro: {e}")

    # =====================================================
    # RESUMO FINAL
    # =====================================================
    print("\n" + "=" * 60)
    print("✅ Migration 004 concluída!")
    print("=" * 60)

    # Estatísticas
    cursor = conn.execute("SELECT COUNT(*) FROM tenant_config")
    print(f"\n📊 Tenants: {cursor.fetchone()[0]}")

    cursor = conn.execute("SELECT COUNT(*) FROM diagnosis_areas")
    print(f"📊 Áreas de diagnóstico: {cursor.fetchone()[0]}")

    cursor = conn.execute("SELECT COUNT(*) FROM agent_configs")
    print(f"🤖 Agentes configurados: {cursor.fetchone()[0]}")

    cursor = conn.execute("SELECT COUNT(*) FROM crm_config")
    print(f"🎯 Configs CRM: {cursor.fetchone()[0]}")

    conn.close()
    print("\n✅ Banco fechado com sucesso!")


if __name__ == "__main__":
    run_migration()
