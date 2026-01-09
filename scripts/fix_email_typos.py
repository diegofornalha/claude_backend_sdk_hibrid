#!/usr/bin/env python3
"""
Corrige erros de digitação em emails

.con → .com
.coml → .com
.comm → .com
@gmail.c → @gmail.com
.vr → .br
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.turso_database import get_db_connection

# Mapeamento de correções
CORRECOES = [
    ('.con', '.com'),
    ('.coml', '.com'),
    ('.comm', '.com'),
    ('@gmail.c', '@gmail.com'),
    ('@hotmail.c', '@hotmail.com'),
    ('@yahoo.com.vr', '@yahoo.com.br'),
    ('.gnail', '.gmail'),
    ('.gmial', '.gmail'),
]

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)

# Buscar emails com erros
conditions = []
for erro, _ in CORRECOES:
    conditions.append(f"email LIKE '%{erro}'")

query = f"""
    SELECT user_id, email, username
    FROM users
    WHERE role = 'lead' AND deleted_at IS NULL AND ({' OR '.join(conditions)})
"""

cursor.execute(query)
leads_com_erro = cursor.fetchall()

print(f"📧 Emails com erros: {len(leads_com_erro)}")
print()

corrected = 0
skipped_duplicate = 0

for lead in leads_com_erro:
    email_original = lead['email']
    email_corrigido = email_original

    # Aplicar correções
    for erro, correto in CORRECOES:
        if erro in email_corrigido:
            email_corrigido = email_corrigido.replace(erro, correto)

    if email_corrigido == email_original:
        continue

    # Verificar se email corrigido já existe
    cursor.execute('SELECT user_id FROM users WHERE email = ? AND deleted_at IS NULL',
                  (email_corrigido,))

    if cursor.fetchone():
        print(f"⏭️  {email_original} → {email_corrigido} (já existe, pulando)")
        skipped_duplicate += 1
        continue

    # Atualizar email e username
    novo_username = f"{email_corrigido.split('@')[0]}_{lead['user_id']}"

    cursor.execute("""
        UPDATE users
        SET email = ?, username = ?
        WHERE user_id = ?
    """, (email_corrigido, novo_username, lead['user_id']))

    if corrected < 20:
        print(f"✅ ID {lead['user_id']}")
        print(f"   Email: {email_original} → {email_corrigido}")
        print(f"   Username: {lead['username']} → {novo_username}")
        print()

    corrected += 1

    if corrected % 100 == 0:
        conn.commit()
        print(f"... {corrected} corrigidos")

conn.commit()
cursor.close()
conn.close()

print(f"{'='*70}")
print(f"✅ Emails corrigidos: {corrected}")
print(f"⏭️  Duplicados (não corrigidos): {skipped_duplicate}")
print(f"{'='*70}")
