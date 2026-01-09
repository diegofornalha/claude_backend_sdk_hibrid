#!/usr/bin/env python3
"""
Limpa usernames problemáticos:
1. Remove #ID duplicados (#80171 #80171 → #80171)
2. Substitui respostas de formulário por email
"""

import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.turso_database import get_db_connection

def parece_resposta(texto):
    """Detecta se é resposta de formulário"""
    palavras_resposta = ['marcar', 'cobrar', 'atender', 'aumentar', 'está', 'fora', 'orçamento', 'vamos', 'não']
    texto_lower = texto.lower()
    return any(palavra in texto_lower for palavra in palavras_resposta)


conn = get_db_connection()
cursor = conn.cursor(dictionary=True)

# Buscar leads com username problemático
cursor.execute('''
    SELECT user_id, username, email
    FROM users
    WHERE role = ? AND deleted_at IS NULL AND (
        username LIKE '%#%#%'  -- IDs duplicados
        OR LENGTH(username) > 50  -- Muito longo
    )
    LIMIT 5000
''', ('lead',))

leads = cursor.fetchall()
print(f"📋 Leads com username problemático: {len(leads)}")

fixed = 0

for lead in leads:
    username = lead['username']

    # Corrigir #ID duplicados
    if '#' in username:
        # Remover duplicações (#80171 #80171 → #80171)
        parts = username.split('#')
        if len(parts) > 2:
            # Pegar nome base e último #ID
            nome_base = parts[0].strip()
            user_id = parts[-1].strip()
            novo_username = f"{nome_base} #{user_id}" if nome_base else f"lead #{user_id}"
        else:
            novo_username = username
    else:
        novo_username = username

    # Se parece resposta de formulário, usar email
    if parece_resposta(novo_username):
        novo_username = lead['email'].split('@')[0]

    # Atualizar se mudou
    if novo_username != username:
        try:
            cursor.execute('UPDATE users SET username = ? WHERE user_id = ?', (novo_username, lead['user_id']))

            if fixed < 20:
                print(f"✅ ID {lead['user_id']}")
                print(f"   {username}")
                print(f"   → {novo_username}")
                print()

            fixed += 1

            if fixed % 100 == 0:
                conn.commit()
                print(f"... {fixed} corrigidos")

        except ValueError as e:
            if "UNIQUE constraint" in str(e):
                # Adicionar sufixo único
                novo_username = f"{novo_username}_{lead['user_id']}"
                cursor.execute('UPDATE users SET username = ? WHERE user_id = ?', (novo_username, lead['user_id']))
                fixed += 1

conn.commit()
cursor.close()
conn.close()

print(f"{'='*70}")
print(f"✅ Total corrigido: {fixed}")
print(f"{'='*70}")
