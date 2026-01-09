#!/usr/bin/env python3
"""
Corrige leads onde username é uma especialidade médica

Problema:
- username: "Endócrino #80171" (especialidade)
- Deveria ser: nome da pessoa ou email@domain

Solução:
- Usar parte do email como nome
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.turso_database import get_db_connection

# Palavras que indicam especialidade/área médica
ESPECIALIDADES = [
    'endócrino', 'pediatra', 'dermatolog', 'cardiolog', 'ortoped',
    'ginecolog', 'oftalmolog', 'neurolog', 'vascular', 'implante',
    'diabetes', 'clínica', 'consultório', 'adultos', 'infantil',
    'estética', 'funcional', 'plástic', 'urgência', 'emergência'
]

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)

# Buscar leads onde username parece especialidade
query_parts = []
params = []

for esp in ESPECIALIDADES:
    query_parts.append("LOWER(username) LIKE ?")
    params.append(f'%{esp}%')

query = f"""
    SELECT user_id, username, email, profession
    FROM users
    WHERE role = 'lead' AND deleted_at IS NULL
    AND ({' OR '.join(query_parts)})
    LIMIT 2000
"""

cursor.execute(query, tuple(params))
leads = cursor.fetchall()

print(f"📋 Leads com especialidade como username: {len(leads)}")
print()

corrected = 0

for lead in leads:
    # Extrair nome base do email
    email_base = lead['email'].split('@')[0]

    # Criar novo username
    novo_username = f"{email_base}_{lead['user_id']}"

    # Mover username atual (especialidade) para profession
    especialidade_atual = lead['username'].replace(f" #{lead['user_id']}", "").strip()

    if lead['profession'] and lead['profession'] != 'Não informado':
        # Se já tem profissão base, adicionar especialidade
        if ' - ' not in lead['profession']:
            nova_profession = f"{lead['profession']} - {especialidade_atual}"
        else:
            nova_profession = lead['profession']  # Já tem especialidade
    else:
        nova_profession = especialidade_atual

    # Atualizar
    try:
        cursor.execute("""
            UPDATE users
            SET username = ?, profession = ?
            WHERE user_id = ?
        """, (novo_username, nova_profession, lead['user_id']))

        if corrected < 20:
            print(f"✅ {lead['email']}")
            print(f"   {lead['username']} → {novo_username}")
            print(f"   {lead['profession']} → {nova_profession}")
            print()

        corrected += 1

        if corrected % 100 == 0:
            conn.commit()
            print(f"... {corrected} corrigidos")

    except Exception as e:
        print(f"❌ Erro em {lead['user_id']}: {e}")

conn.commit()
cursor.close()
conn.close()

print(f"{'='*70}")
print(f"✅ Total corrigido: {corrected}")
print(f"{'='*70}")
