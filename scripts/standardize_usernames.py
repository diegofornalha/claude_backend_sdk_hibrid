#!/usr/bin/env python3
"""
Padroniza TODOS os usernames para o formato: email_ID

Substitui qualquer username (nome real, especialidade, etc) por email_ID
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.turso_database import get_db_connection

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)

# Buscar TODOS os leads
cursor.execute('''
    SELECT user_id, username, email
    FROM users
    WHERE role = ? AND deleted_at IS NULL
''', ('lead',))

leads = cursor.fetchall()
print(f"📋 Total de leads: {len(leads)}")
print(f"🔧 Padronizando para formato: email_ID")
print()

updated = 0

for lead in leads:
    # Gerar username padrão
    email_base = lead['email'].split('@')[0]
    novo_username = f"{email_base}_{lead['user_id']}"

    # Atualizar se diferente
    if lead['username'] != novo_username:
        try:
            cursor.execute('UPDATE users SET username = ? WHERE user_id = ?',
                          (novo_username, lead['user_id']))
            updated += 1

            if updated <= 20:
                print(f"✅ {lead['username']} → {novo_username}")

            if updated % 1000 == 0:
                conn.commit()
                print(f"... {updated} atualizados")

        except Exception as e:
            if "UNIQUE constraint" in str(e):
                # Adicionar sufixo extra
                novo_username = f"{email_base}_{lead['user_id']}_u"
                cursor.execute('UPDATE users SET username = ? WHERE user_id = ?',
                              (novo_username, lead['user_id']))
                updated += 1

conn.commit()
cursor.close()
conn.close()

print(f"\n{'='*70}")
print(f"✅ Total padronizado: {updated}")
print(f"⏭️  Já estavam corretos: {len(leads) - updated}")
print(f"{'='*70}")
