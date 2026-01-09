#!/usr/bin/env python3
"""
Deduplicação de leads por telefone

Estratégia:
1. Agrupar por telefone
2. Escolher lead master (mais dados preenchidos)
3. Mesclar informações dos outros
4. Marcar duplicados como deleted
"""

import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.turso_database import get_db_connection

def score_completude(lead):
    """Calcula score de completude do lead (quantos campos preenchidos)"""
    score = 0
    if lead.get('username') and '@' not in lead['username']:
        score += 3  # Nome real vale mais
    if lead.get('email'):
        score += 2
    if lead.get('phone_number'):
        score += 2
    if lead.get('profession') and lead['profession'] != 'Não informado':
        score += 3
    if lead.get('registration_date'):
        score += 1  # Mais recente
    return score


conn = get_db_connection()
cursor = conn.cursor(dictionary=True)

# Buscar telefones duplicados
cursor.execute('''
    SELECT phone_number, COUNT(*) as qtd
    FROM users
    WHERE role = ? AND phone_number IS NOT NULL AND phone_number != ?
    GROUP BY phone_number
    HAVING COUNT(*) > 1
    ORDER BY qtd DESC
''', ('lead', ''))

duplicados = cursor.fetchall()
print(f"📱 Total de telefones duplicados: {len(duplicados)}")
print()

merged = 0
deleted = 0

for dup in duplicados:
    telefone = dup['phone_number']

    # Buscar todos os leads com esse telefone
    cursor.execute('''
        SELECT user_id, username, email, phone_number, profession, registration_date
        FROM users
        WHERE phone_number = ?
        ORDER BY user_id
    ''', (telefone,))

    leads = cursor.fetchall()

    if len(leads) < 2:
        continue

    # Escolher master (lead mais completo)
    leads_scored = [(lead, score_completude(lead)) for lead in leads]
    leads_scored.sort(key=lambda x: x[1], reverse=True)

    master = leads_scored[0][0]
    duplicates = [l[0] for l in leads_scored[1:]]

    print(f"📞 {telefone} ({len(leads)} duplicados)")
    print(f"  ✅ MASTER: ID {master['user_id']} - {master['username']} ({master['email']})")

    # Mesclar dados dos duplicados para o master
    updates = []
    params = []

    for duplicate in duplicates:
        # Se master não tem profissão mas duplicado tem
        if (not master['profession'] or master['profession'] == 'Não informado') and duplicate['profession']:
            if 'profession' not in [u.split('=')[0].strip() for u in updates]:
                updates.append("profession = %s")
                params.append(duplicate['profession'])

        # Marcar duplicado como deleted
        now = datetime.now().isoformat()
        cursor.execute('''
            UPDATE users
            SET deleted_at = ?, account_status = ?
            WHERE user_id = ?
        ''', (now, 'merged_duplicate', duplicate['user_id']))

        deleted += 1
        print(f"  🗑️  DEL: ID {duplicate['user_id']} - {duplicate['email']}")

    # Atualizar master se necessário
    if updates:
        params.append(master['user_id'])
        query = f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?"
        cursor.execute(query, tuple(params))

    merged += 1

    if merged % 10 == 0:
        conn.commit()
        print()

    print()

conn.commit()
cursor.close()
conn.close()

print(f"{'='*70}")
print(f"✅ Telefones mesclados: {merged}")
print(f"🗑️  Leads duplicados removidos: {deleted}")
print(f"{'='*70}")
