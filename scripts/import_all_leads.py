#!/usr/bin/env python3
"""
Importa leads válidos do all_leads.json
Filtra apenas leads com email + (nome OU profissão)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.turso_database import get_db_connection

all_leads_file = Path("/home/diagnostico/elementor-backup/backups/20251216_125222/all_leads.json")

print("📋 Carregando all_leads.json...")
with open(all_leads_file, "r") as f:
    backup_data = json.load(f)

leads = backup_data.get("leads", [])
print(f"📊 Total de leads no arquivo: {len(leads)}")
print("🔍 Filtrando leads válidos (email + nome/profissão)...\n")

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)

imported = 0
skipped_duplicate = 0
skipped_invalid = 0

for i, lead_data in enumerate(leads, 1):
    try:
        elementor = lead_data.get("elementor", {})

        email = elementor.get("email", "").strip()
        nome = elementor.get("nome", "").strip()
        telefone = elementor.get("telefone", "").strip()
        profissao = elementor.get("profissao", "").strip()

        # Pular se não tem email
        if not email:
            skipped_invalid += 1
            continue

        # Usar email como nome se não tiver nada
        if not nome and not profissao:
            nome = email.split('@')[0]  # Usar parte antes do @
            profissao = "Não informado"
        elif not nome:
            nome = profissao
        elif not profissao:
            profissao = "Não informado"

        # Verificar duplicado
        cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            skipped_duplicate += 1
            continue

        # Inserir
        cursor.execute("""
            INSERT INTO users (username, email, phone_number, profession, role, account_status, password_hash)
            VALUES (%s, %s, %s, %s, 'lead', 'lead', 'no_password_lead')
        """, (nome, email, telefone or None, profissao or "Não informado"))

        cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
        lead_id = cursor.fetchone()["user_id"]

        notes = json.dumps({
            "elementor_data": {
                "source": "elementor_all_leads_backup",
                "form_name": elementor.get("form_name", ""),
                "profissao": profissao,
                "submission_id": str(elementor.get("submission_id", "")),
                "utm": {
                    "source": elementor.get("utm_source", ""),
                    "medium": elementor.get("utm_medium", ""),
                    "campaign": elementor.get("utm_campaign", ""),
                    "content": elementor.get("utm_content", ""),
                    "term": elementor.get("utm_term", "")
                },
                "ip_address": elementor.get("ip", ""),
                "landing_page_url": elementor.get("referer_url", ""),
                "captured_at": elementor.get("created_at", "")
            }
        }, ensure_ascii=False)

        cursor.execute("""
            INSERT INTO crm_lead_state (lead_id, current_state, owner_team, notes)
            VALUES (%s, 'novo', 'marketing', %s)
        """, (lead_id, notes))

        imported += 1

        # Log e commit a cada 1000
        if imported % 1000 == 0:
            conn.commit()
            print(f"[{i}/{len(leads)}] ✅ {imported} importados | ⏭️ {skipped_duplicate} duplicados | 🗑️ {skipped_invalid} inválidos")

    except Exception as e:
        if imported < 10:
            print(f"❌ Erro no lead {i}: {e}")

# Commit final
conn.commit()
cursor.close()
conn.close()

print(f"\n{'='*70}")
print(f"✅ Importados: {imported}")
print(f"⏭️  Duplicados: {skipped_duplicate}")
print(f"🗑️  Inválidos (sem dados): {skipped_invalid}")
print(f"📊 Total processado: {imported + skipped_duplicate + skipped_invalid}/{len(leads)}")
print(f"{'='*70}")
