#!/usr/bin/env python3
"""
Deduplicação de leads por email

Emails devem ser únicos - mesclar registros duplicados
"""

import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.turso_database import get_db_connection

def score_completude(lead):
    """Score baseado em completude"""
    score = 0
    if lead.get('username') and '@' not in lead['username'] and '#' in lead['username']:
        score += 5  # Nome corrigido
    if lead.get('phone_number'):
        score += 3
    if lead.get('profession') and lead['profession'] != 'Não informado':
        score += 3
    # Mais antigo = mais confiável
    if lead.get('user_id'):
        score += (100000 - lead['user_id']) / 10000
    return score


conn = get_db_connection()
cursor = conn.cursor(dictionary=True)

# Buscar emails duplicados (case insensitive)
cursor.execute('''
    SELECT LOWER(email) as email_lower, COUNT(*) as qtd
    FROM users
    WHERE role = ? AND deleted_at IS NULL
    GROUP BY LOWER(email)
    HAVING COUNT(*) > 1
    ORDER BY qtd DESC
''', ('lead',))

duplicados = cursor.fetchall()
print(f"📧 Total de emails duplicados: {len(duplicados)}")
print()

merged = 0
deleted = 0

for dup in duplicados:
    email_lower = dup['email_lower']

    # Buscar todos os leads com esse email (case insensitive)
    cursor.execute('''
        SELECT user_id, username, email, phone_number, profession, registration_date
        FROM users
        WHERE LOWER(email) = ? AND deleted_at IS NULL
        ORDER BY user_id
    ''', (email_lower,))

    leads = cursor.fetchall()

    if len(leads) < 2:
        continue

    # Escolher master (mais completo)
    leads_scored = [(lead, score_completude(lead)) for lead in leads]
    leads_scored.sort(key=lambda x: x[1], reverse=True)

    master = leads_scored[0][0]
    duplicates = [l[0] for l in leads_scored[1:]]

    print(f"📧 {email_lower} ({len(leads)} duplicados)")
    print(f"  ✅ MASTER: ID {master['user_id']} - {master['username']} ({master['email']})")

    for duplicate in duplicates:
        # Marcar como deleted
        now = datetime.now().isoformat()
        cursor.execute('''
            UPDATE users
            SET deleted_at = ?, account_status = ?
            WHERE user_id = ?
        ''', (now, 'merged_duplicate', duplicate['user_id']))

        deleted += 1
        print(f"  🗑️  DEL: ID {duplicate['user_id']} - {duplicate['email']}")

    merged += 1

    if merged % 10 == 0:
        conn.commit()

    print()

conn.commit()
cursor.close()
conn.close()

print(f"{'='*70}")
print(f"✅ Emails mesclados: {merged}")
print(f"🗑️  Duplicados removidos: {deleted}")
print(f"{'='*70}")
