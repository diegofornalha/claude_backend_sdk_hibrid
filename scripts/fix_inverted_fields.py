#!/usr/bin/env python3
"""
Corrige campos invertidos do Typeform

Problema:
- username = especialidade (Diabetes, Implante, Vascular)
- profession = profissao - nome (Nutricionista - Maria Fernanda)

Solução:
- username = nome #ID (Maria Fernanda #79025)
- profession = profissao - especialidade (Nutricionista - Diabetes)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.turso_database import get_db_connection

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)

# Buscar leads onde profession tem " - " (indica que tem nome junto)
cursor.execute("""
    SELECT user_id, username, email, profession
    FROM users
    WHERE role = 'lead' AND profession LIKE '% - %' AND deleted_at IS NULL
    ORDER BY user_id DESC
""")

leads = cursor.fetchall()
print(f"📋 Leads a corrigir: {len(leads)}")
print()

corrected = 0

for lead in leads:
    # Separar profissão base e nome
    parts = lead['profession'].split(' - ', 1)
    profissao_base = parts[0].strip()  # Ex: Nutricionista
    nome_correto = parts[1].strip()     # Ex: Maria Fernanda

    especialidade_atual = lead['username']  # Ex: Diabetes

    # Montar nova profissão: profissao - especialidade
    if len(especialidade_atual) < 30 and especialidade_atual[0].isupper():
        nova_profissao = f"{profissao_base} - {especialidade_atual}"
    else:
        # Se username não parece especialidade, só usar profissão base
        nova_profissao = profissao_base

    # Username único: Nome #ID
    username_unico = f"{nome_correto} #{lead['user_id']}"

    # Atualizar
    cursor.execute("""
        UPDATE users
        SET username = %s, profession = %s
        WHERE user_id = %s
    """, (username_unico, nova_profissao, lead['user_id']))

    if corrected < 20:
        print(f"✅ {lead['email']}")
        print(f"   {lead['username']} → {username_unico}")
        print(f"   {lead['profession']} → {nova_profissao}")
        print()

    corrected += 1

    if corrected % 100 == 0:
        conn.commit()
        print(f"... {corrected} leads corrigidos")

conn.commit()
cursor.close()
conn.close()

print(f"{'='*70}")
print(f"✅ Total de leads corrigidos: {corrected}")
print(f"{'='*70}")
