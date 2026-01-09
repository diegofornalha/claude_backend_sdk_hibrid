#!/usr/bin/env python3
"""
Corrige nomes que são respostas de formulário em vez de nomes de pessoas
"""

import sys
from pathlib import Path
import re

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.turso_database import get_db_connection

def parece_resposta_formulario(texto):
    """Detecta se texto é resposta de pergunta"""
    if not texto:
        return False

    # Palavras comuns em respostas de formulário
    palavras_resposta = [
        'atender', 'cobrar', 'mais', 'menos', 'paciente', 'aumentar',
        'melhorar', 'conquistar', 'manter', 'criar', 'desenvolver',
        'e ', 'de ', 'o ', 'a ', 'muito', 'pouco', 'sem', 'com '
    ]

    texto_lower = texto.lower()

    # Se tem 2 ou mais dessas palavras, provavelmente é resposta
    count = sum(1 for palavra in palavras_resposta if palavra in texto_lower)
    if count >= 2:
        return True

    # Se tem verbo no infinitivo no início
    if re.match(r'^(Atender|Aumentar|Melhorar|Conquistar|Manter|Criar|Desenvolver)', texto):
        return True

    return False


conn = get_db_connection()
cursor = conn.cursor(dictionary=True)

# Buscar leads com nomes suspeitos
cursor.execute("""
    SELECT user_id, username, email, profession
    FROM users
    WHERE role = 'lead' AND LENGTH(username) > 20
    LIMIT 1000
""")

leads = cursor.fetchall()
print(f"📋 Leads com nomes longos: {len(leads)}")
print()

corrected = 0

for lead in leads:
    if parece_resposta_formulario(lead['username']):
        # Extrair nome da profissão se tiver " - Nome"
        profession = lead['profession'] or ''

        novo_nome = None

        if ' - ' in profession:
            # Separar profissão e especialidade/nome
            parts = profession.split(' - ', 1)
            possivel_nome = parts[1].strip()

            # Verificar se parece nome
            if possivel_nome and len(possivel_nome) < 50:
                novo_nome = possivel_nome
                nova_profissao = parts[0]  # Só a profissão base

                cursor.execute("""
                    UPDATE users
                    SET username = %s, profession = %s
                    WHERE user_id = %s
                """, (novo_nome, nova_profissao, lead['user_id']))

                print(f"✅ ID {lead['user_id']}: {lead['username'][:40]} → {novo_nome}")
                corrected += 1

        if not novo_nome:
            # Usar parte do email como fallback
            novo_nome = lead['email'].split('@')[0]
            cursor.execute("UPDATE users SET username = %s WHERE user_id = %s", (novo_nome, lead['user_id']))
            print(f"⚠️  ID {lead['user_id']}: {lead['username'][:40]} → {novo_nome} (email)")
            corrected += 1

conn.commit()
cursor.close()
conn.close()

print(f"\n✅ Leads corrigidos: {corrected}")
