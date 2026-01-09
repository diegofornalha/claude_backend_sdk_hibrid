#!/usr/bin/env python3
"""
Importação rápida de leads do Elementor (sem processamento via SDK)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.turso_database import get_db_connection

forms_dir = Path("/home/diagnostico/elementor-backup/data/forms")

# Coletar JSONs
json_files = []
for form_dir in forms_dir.iterdir():
    if form_dir.is_dir():
        for json_file in form_dir.glob("*.json"):
            if json_file.name != "index.json":
                json_files.append(json_file)

print(f"📋 Importando {len(json_files)} leads...")

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)

imported = 0
skipped = 0

for json_file in json_files:
    with open(json_file, "r") as f:
        data = json.load(f)

    email = data.get("email", "")
    nome = data.get("nome", data.get("name", ""))
    telefone = data.get("telefone", "")
    profissao = data.get("profissao", "Não informado")

    if not email or not nome:
        continue

    # Verificar duplicado
    cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
    if cursor.fetchone():
        skipped += 1
        continue

    # Inserir (leads não precisam de senha real)
    cursor.execute("""
        INSERT INTO users (username, email, phone_number, profession, role, account_status, password_hash)
        VALUES (%s, %s, %s, %s, 'lead', 'lead', 'no_password_lead')
    """, (nome, email, telefone, profissao))

    # Obter ID inserido
    cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
    lead_id = cursor.fetchone()["user_id"]

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
            "captured_at": data.get("created_at", "")
        }
    }, ensure_ascii=False)

    cursor.execute("""
        INSERT INTO crm_lead_state (lead_id, current_state, owner_team, notes)
        VALUES (%s, 'novo', 'marketing', %s)
    """, (lead_id, notes))

    imported += 1
    print(f"✅ {nome} - {profissao}")

conn.commit()
cursor.close()
conn.close()

print(f"\n📊 Importados: {imported}, Já existiam: {skipped}")
