#!/usr/bin/env python3
"""
Script para importar leads do backup do Elementor para o CRM Nanda

Lê os arquivos JSON completos e processa cada lead via orquestrador Claude Agent SDK
"""

import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

# Adicionar path do projeto
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.turso_database import get_db_connection
from core.crm_agent_orchestrator import get_orchestrator


async def import_lead_from_json(json_file: Path) -> dict:
    """
    Importa um lead do arquivo JSON do Elementor

    Args:
        json_file: Arquivo JSON com dados completos do lead

    Returns:
        Resultado do processamento
    """
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    conn = get_db_connection()
    if not conn:
        return {"success": False, "error": "Database connection failed"}

    try:
        cursor = conn.cursor(dictionary=True)

        # Extrair dados
        email = data.get("email", "")
        nome = data.get("nome", data.get("name", ""))
        telefone = data.get("telefone", data.get("phone", ""))
        profissao = data.get("profissao", "Não informado")

        if not email or not nome:
            return {"success": False, "error": "Email ou nome faltando"}

        # Verificar se já existe
        cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
        existing = cursor.fetchone()

        if existing:
            print(f"⏭️  Lead já existe: {nome} ({email})")
            cursor.close()
            conn.close()
            return {"success": True, "already_exists": True, "lead_id": existing["user_id"]}

        # Criar novo lead
        cursor.execute("""
            INSERT INTO users (username, email, phone_number, profession, role, account_status)
            VALUES (%s, %s, %s, %s, 'lead', 'lead')
        """, (nome, email, telefone, profissao))

        lead_id = cursor.lastrowid

        # Criar estado CRM com dados completos
        notes = json.dumps({
            "elementor_data": {
                "source": "elementor_backup",
                "form_name": data.get("form_name", ""),
                "profissao": profissao,
                "utm": {
                    "source": data.get("utm_source", ""),
                    "medium": data.get("utm_medium", ""),
                    "campaign": data.get("utm_campaign", ""),
                    "content": data.get("utm_content", ""),
                    "term": data.get("utm_term", "")
                },
                "ip_address": data.get("ip", ""),
                "landing_page_url": data.get("referer_url", ""),
                "captured_at": data.get("created_at", data.get("received_at", ""))
            }
        }, ensure_ascii=False)

        cursor.execute("""
            INSERT INTO crm_lead_state (lead_id, current_state, owner_team, notes)
            VALUES (%s, 'novo', 'marketing', %s)
        """, (lead_id, notes))

        conn.commit()
        cursor.close()
        conn.close()

        print(f"✅ Lead criado: {nome} - {profissao} ({email}) - ID: {lead_id}")

        # Processar via orquestrador (scoring + tasks + state)
        print(f"   🤖 Processando via Claude Agent SDK...")
        orchestrator = get_orchestrator()
        result = await orchestrator.process_new_lead(lead_id)

        if result.get("success"):
            print(f"   ✅ Score: {result.get('score')}, Temp: {result.get('temperatura')}")
        else:
            print(f"   ⚠️  Processamento parcial: {result.get('error', 'N/A')}")

        return {
            "success": True,
            "lead_id": lead_id,
            "score": result.get("score"),
            "temperatura": result.get("temperatura")
        }

    except Exception as e:
        if conn:
            conn.close()
        print(f"❌ Erro ao importar lead: {e}")
        return {"success": False, "error": str(e)}


async def main():
    """Processa todos os leads dos JSONs do backup"""

    forms_dir = Path("/home/diagnostico/elementor-backup/data/forms")

    if not forms_dir.exists():
        print(f"❌ Diretório não encontrado: {forms_dir}")
        return

    print("=" * 70)
    print("IMPORTAÇÃO DE LEADS DO ELEMENTOR (BACKUP)")
    print("=" * 70)

    # Coletar todos os JSONs (exceto index.json)
    json_files = []
    for form_dir in forms_dir.iterdir():
        if form_dir.is_dir():
            for json_file in form_dir.glob("*.json"):
                if json_file.name != "index.json":
                    json_files.append(json_file)

    json_files.sort()  # Ordenar por nome

    print(f"📋 Total de leads encontrados: {len(json_files)}")
    print()

    # Processar cada arquivo
    imported = 0
    skipped = 0
    errors = 0

    for i, json_file in enumerate(json_files, 1):
        try:
            print(f"[{i}/{len(json_files)}] Processando: {json_file.parent.name}/{json_file.name}")

            result = await import_lead_from_json(json_file)

            if result.get("success"):
                if result.get("already_exists"):
                    skipped += 1
                else:
                    imported += 1
                    # Delay para não sobrecarregar API do Claude
                    print(f"   ⏳ Aguardando 3s...")
                    await asyncio.sleep(3)
            else:
                errors += 1

            print()  # Linha em branco entre leads

        except Exception as e:
            print(f"❌ Erro ao processar {json_file.name}: {e}")
            errors += 1
            print()

    print("=" * 70)
    print("RESUMO DA IMPORTAÇÃO")
    print("=" * 70)
    print(f"✅ Importados e processados: {imported}")
    print(f"⏭️  Já existiam no banco: {skipped}")
    print(f"❌ Erros: {errors}")
    print(f"📊 Total: {imported + skipped + errors}/{len(json_files)}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
