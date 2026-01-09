#!/usr/bin/env python3
"""
Merge de dados do Typeform com leads do Elementor

Estratégia:
1. MERGE: Email existe → ATUALIZAR profissão/nome/telefone (sem sobrescrever)
2. CREATE: Email novo → CRIAR lead com dados do Typeform
"""

import json
import sys
import time
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.turso_database import get_db_connection

typeform_dir = Path("/home/diagnostico/typeform/backup/20251216_111632/forms")

print("=" * 70)
print("MERGE TYPEFORM → ELEMENTOR LEADS")
print("=" * 70)

# Coletar todos os responses.json
response_files = list(typeform_dir.glob("*/responses.json"))
print(f"📋 Total de formulários: {len(response_files)}\n")

# Dicionário para acumular dados por email
leads_by_email = defaultdict(lambda: {
    "nome": None,
    "profissao": None,
    "especialidade": None,
    "telefone": None,
    "typeform_responses": 0
})

total_responses = 0

# Processar cada formulário
print("📥 Processando formulários Typeform...")
for i, resp_file in enumerate(response_files, 1):
    try:
        with open(resp_file, "r") as f:
            responses = json.load(f)

        for response in responses:
            total_responses += 1

            # Extrair dados
            email = None
            nome = None
            profissao = None
            especialidade = None
            telefone = None

            # Ordem comum dos campos no Typeform:
            # 1. profissao (choice)
            # 2. especialidade (text)
            # 3-6. outras perguntas
            # 7. nome (text curto)
            # 8. telefone (phone_number)
            # 9. email (email)

            text_fields = []

            for answer in response.get("answers", []):
                ans_type = answer.get("type")

                if ans_type == "email":
                    email = answer.get("email", "").strip().lower()

                elif ans_type == "choice":
                    choice = answer.get("choice", {}).get("label", "").strip()
                    if choice in ["Médico", "Dentista", "Fisioterapeuta", "Nutricionista", "Psicólogo", "Fonoaudiólogo"]:
                        if not profissao:
                            profissao = choice

                elif ans_type == "phone_number":
                    telefone = answer.get("phone_number", "").strip()

                elif ans_type == "text":
                    text = answer.get("text", "").strip()
                    if text and len(text) < 50:
                        text_fields.append(text)

            # Identificar nome vs especialidade vs respostas
            def parece_nome(texto):
                """Verifica se texto parece um nome de pessoa"""
                if not texto or len(texto) > 50:
                    return False

                # Remover se tem palavras comuns de respostas
                palavras_resposta = ['e ', 'de ', 'o ', 'a ', 'mais', 'menos', 'muito', 'pouco', 'sem', 'com']
                texto_lower = texto.lower()
                if any(palavra in texto_lower for palavra in palavras_resposta):
                    return False

                # Nome geralmente:
                # - Tem primeira letra maiúscula
                # - Só letras (e espaços)
                # - Curto (< 30 caracteres)
                if len(texto) < 30 and texto[0].isupper():
                    # Verificar se é só letras/espaços
                    import re
                    if re.match(r'^[A-ZÁÀÂÃÉÈÊÍÏÓÔÕÖÚÇÑ][a-záàâãéèêíïóôõöúçñA-ZÁÀÂÃÉÈÊÍÏÓÔÕÖÚÇÑ\s]+$', texto):
                        return True

                return False

            # Filtrar apenas textos que parecem nomes
            nomes_possiveis = [t for t in text_fields if parece_nome(t)]
            especialidades_possiveis = [t for t in text_fields if not parece_nome(t) and len(t) < 30]

            # Atribuir
            if nomes_possiveis:
                nome = nomes_possiveis[0]
            if especialidades_possiveis and profissao:
                especialidade = especialidades_possiveis[0]

            # Acumular dados
            if email:
                lead = leads_by_email[email]
                lead["typeform_responses"] += 1

                if nome and not lead["nome"]:
                    lead["nome"] = nome
                if profissao and not lead["profissao"]:
                    lead["profissao"] = profissao
                if especialidade and not lead["especialidade"]:
                    lead["especialidade"] = especialidade
                if telefone and not lead["telefone"]:
                    lead["telefone"] = telefone

        if i % 20 == 0:
            print(f"  [{i}/{len(response_files)}] {total_responses} respostas, {len(leads_by_email)} emails únicos")

    except Exception as e:
        pass  # Ignorar erros

print(f"\n✅ Total de respostas: {total_responses}")
print(f"📧 Emails únicos: {len(leads_by_email)}")

# Fazer merge/create em lotes pequenos (evitar lock)
print(f"\n{'='*70}")
print("PROCESSANDO BANCO DE DADOS (em lotes)")
print(f"{'='*70}\n")

updated = 0
created = 0
skipped = 0
errors = 0

batch_size = 50
batch_num = 0

emails = list(leads_by_email.items())

for i in range(0, len(emails), batch_size):
    batch = emails[i:i+batch_size]
    batch_num += 1

    # Nova conexão para cada lote
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    for email, typeform_data in batch:
        try:
            # Buscar lead existente
            cursor.execute("""
                SELECT user_id, username, profession, phone_number
                FROM users
                WHERE email = %s
            """, (email,))

            existing = cursor.fetchone()

            if existing:
                # MERGE: Atualizar apenas campos vazios
                updates = []
                params = []

                # Profissão
                if (not existing["profession"] or existing["profession"] == "Não informado") and typeform_data["profissao"]:
                    prof = typeform_data["profissao"]
                    if typeform_data["especialidade"]:
                        prof += f" - {typeform_data['especialidade']}"
                    updates.append("profession = %s")
                    params.append(prof)

                # Nome
                if ("@" in existing["username"] or existing["username"] == "Não informado") and typeform_data["nome"]:
                    updates.append("username = %s")
                    params.append(typeform_data["nome"])

                # Telefone
                if not existing["phone_number"] and typeform_data["telefone"]:
                    updates.append("phone_number = %s")
                    params.append(typeform_data["telefone"])

                if updates:
                    params.append(existing["user_id"])
                    cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id = %s", tuple(params))
                    updated += 1
                else:
                    skipped += 1

            else:
                # CREATE: Novo lead do Typeform
                if typeform_data["profissao"]:  # Só criar se tiver profissão
                    nome = typeform_data["nome"] or email.split('@')[0]
                    prof = typeform_data["profissao"]
                    if typeform_data["especialidade"]:
                        prof += f" - {typeform_data['especialidade']}"

                    cursor.execute("""
                        INSERT INTO users (username, email, phone_number, profession, role, account_status, password_hash)
                        VALUES (%s, %s, %s, %s, 'lead', 'lead', 'no_password_lead')
                    """, (nome, email, typeform_data["telefone"], prof))

                    cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
                    lead_id = cursor.fetchone()["user_id"]

                    # Criar estado CRM
                    notes = json.dumps({"typeform_data": {"source": "typeform_backup"}}, ensure_ascii=False)
                    cursor.execute("""
                        INSERT INTO crm_lead_state (lead_id, current_state, owner_team, notes)
                        VALUES (%s, 'novo', 'marketing', %s)
                    """, (lead_id, notes))

                    created += 1
                else:
                    skipped += 1

        except Exception as e:
            errors += 1

    # Commit do lote
    conn.commit()
    cursor.close()
    conn.close()

    print(f"  Lote {batch_num}: ✅ {updated} atualizados, 🆕 {created} novos, ⏭️ {skipped} ignorados")

    # Pequeno delay entre lotes
    time.sleep(0.1)

print(f"\n{'='*70}")
print("RESUMO FINAL")
print(f"{'='*70}")
print(f"✅ MERGE (atualizados): {updated}")
print(f"🆕 CREATE (novos): {created}")
print(f"⏭️  Ignorados: {skipped}")
print(f"❌ Erros: {errors}")
print(f"📊 Total: {updated + created + skipped}/{len(leads_by_email)}")
print(f"{'='*70}")
