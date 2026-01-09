#!/usr/bin/env python3
"""
Migration 005: Agnostic Configuration

Torna o sistema agnóstico, permitindo configuração para qualquer nicho:
- Adiciona campos de contexto ao tenant_config
- Renomeia áreas de diagnóstico para termos genéricos
"""

import os
import sys
import sqlite3
import json
from datetime import datetime

# Path do banco
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'crm.db')


def run_migration():
    """Executa a migração."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print("🔧 Migration 005: Agnostic Configuration")
    print("=" * 60)

    # =====================================================
    # 1. ADICIONAR COLUNAS AO tenant_config
    # =====================================================
    print("\n📋 Adicionando colunas de contexto agnóstico...")

    # Lista de colunas a adicionar
    new_columns = [
        ("target_audience", "TEXT DEFAULT 'profissionais e empresários'"),
        ("business_context", "TEXT DEFAULT 'ajudar profissionais e empresários a crescerem seus negócios'"),
        ("client_term", "TEXT DEFAULT 'cliente'"),
        ("client_term_plural", "TEXT DEFAULT 'clientes'"),
        ("service_term", "TEXT DEFAULT 'serviço'"),
        ("team_term", "TEXT DEFAULT 'equipe'"),
        ("audience_goals", "TEXT"),  # JSON array
    ]

    # Verificar quais colunas já existem
    cursor = conn.execute("PRAGMA table_info(tenant_config)")
    existing_columns = {row['name'] for row in cursor.fetchall()}

    for col_name, col_def in new_columns:
        if col_name not in existing_columns:
            try:
                conn.execute(f"ALTER TABLE tenant_config ADD COLUMN {col_name} {col_def}")
                print(f"  ✅ Coluna '{col_name}' adicionada")
            except sqlite3.OperationalError as e:
                print(f"  ⚠️ Coluna '{col_name}' já existe ou erro: {e}")
        else:
            print(f"  ⏭️ Coluna '{col_name}' já existe")

    # =====================================================
    # 2. ATUALIZAR VALORES DEFAULT
    # =====================================================
    print("\n📝 Atualizando valores default...")

    default_goals = json.dumps([
        "Melhorar seu posicionamento de mercado",
        "Aumentar sua precificação e faturamento",
        "Desenvolver estratégias de vendas",
        "Criar estratégias de atração e fidelização",
        "Otimizar a experiência do cliente",
        "Gerenciar melhor sua equipe"
    ], ensure_ascii=False)

    conn.execute("""
        UPDATE tenant_config
        SET audience_goals = ?
        WHERE audience_goals IS NULL
    """, (default_goals,))
    print("  ✅ audience_goals definido")

    # =====================================================
    # 3. RENOMEAR ÁREAS DE DIAGNÓSTICO
    # =====================================================
    print("\n📊 Renomeando áreas de diagnóstico para termos genéricos...")

    areas_update = [
        {
            "area_key": "mentalidade",
            "area_name": "Estratégia de Vendas",
            "area_description": "Avaliação da mentalidade e estratégias de vendas"
        },
        {
            "area_key": "paciente_plano",
            "area_name": "Cliente e Proposta de Valor",
            "area_description": "Analisa como você estrutura ofertas e atende clientes"
        },
        {
            "area_key": "jornada_encantamento",
            "area_name": "Experiência do Cliente",
            "area_description": "Avalia a jornada e experiência do seu cliente"
        },
        {
            "area_key": "atracao_fidelizacao",
            "area_name": "Marketing e Retenção",
            "area_description": "Estratégias de atração e fidelização de clientes"
        },
        {
            "area_key": "secretarias_auxiliares",
            "area_name": "Equipe e Processos",
            "area_description": "Gestão de equipe e processos operacionais"
        },
        {
            "area_key": "seu_negocio",
            "area_name": "Gestão do Negócio",
            "area_description": "Gestão financeira e operacional do negócio"
        },
        {
            "area_key": "dominio_tecnico",
            "area_name": "Expertise Técnica",
            "area_description": "Domínio técnico e conhecimento especializado"
        },
    ]

    for area in areas_update:
        conn.execute("""
            UPDATE diagnosis_areas
            SET area_name = ?,
                description = ?
            WHERE area_key = ?
        """, (area["area_name"], area["area_description"], area["area_key"]))
        print(f"  ✅ {area['area_key']} → {area['area_name']}")

    # =====================================================
    # 4. COMMIT
    # =====================================================
    conn.commit()
    conn.close()

    print("\n" + "=" * 60)
    print("✅ Migration 005 concluída com sucesso!")
    print("\nResumo:")
    print("  - 7 novos campos adicionados ao tenant_config")
    print("  - 7 áreas de diagnóstico renomeadas")


def rollback():
    """Reverte a migração (para debug)."""
    conn = sqlite3.connect(DB_PATH)

    print("🔙 Rollback Migration 005...")

    # Reverter nomes das áreas
    areas_rollback = [
        ("mentalidade", "Mentalidade High Ticket", "Avaliação da mentalidade para vendas de alto valor"),
        ("paciente_plano", "Paciente e Plano de Tratamento", "Analisa estruturação de tratamentos e relacionamento"),
        ("jornada_encantamento", "Jornada e Encantamento", "Avalia a experiência do paciente na jornada"),
        ("atracao_fidelizacao", "Atração e Fidelização", "Estratégias de marketing e retenção de pacientes"),
        ("secretarias_auxiliares", "Secretárias e Auxiliares", "Gestão de equipe de apoio"),
        ("seu_negocio", "Seu Negócio", "Gestão financeira e operacional"),
        ("dominio_tecnico", "Domínio Técnico", "Conhecimento técnico e clínico"),
    ]

    for area_key, area_name, description in areas_rollback:
        conn.execute("""
            UPDATE diagnosis_areas
            SET area_name = ?, description = ?
            WHERE area_key = ?
        """, (area_name, description, area_key))
        print(f"  ✅ {area_key} revertido")

    conn.commit()
    conn.close()
    print("✅ Rollback concluído")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        rollback()
    else:
        run_migration()
