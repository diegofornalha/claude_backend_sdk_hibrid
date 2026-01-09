"""
Script para popular banco com dados de teste para sistema de mentoria
Cria: 1 admin (Nanda), 3 mentores, 9 mentorados (3 por mentor)
"""

import sqlite.connector
import os
import sys
from datetime import datetime

# Configuração do banco
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'crm'),
    'port': int(os.getenv('DB_PORT', '3306'))
}


def hash_password_simple(password: str) -> str:
    """
    Hash usando PBKDF2 (mesmo do app.py)
    Senha: password123 para todos
    """
    import hashlib
    import base64
    import os

    # Gerar salt aleatório
    salt = os.urandom(32)

    # PBKDF2-HMAC-SHA256 com 100k iterações (mesmo do app.py)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt,
        100000  # Sem dklen - usa padrão de 32 bytes
    )

    # Armazenar salt + key em base64
    storage = salt + key
    return base64.b64encode(storage).decode('ascii')


def main():
    print("🚀 Populando banco com dados de mentoria...")

    try:
        conn = sqlite.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # Senha padrão para todos: password123
        password_hash = hash_password_simple('password123')

        # =====================================================
        # 1. CRIAR ADMIN (Nanda)
        # =====================================================
        print("\n👑 Criando Admin (Nanda)...")

        cursor.execute("""
            INSERT INTO users (username, email, password_hash, phone_number, account_status, role)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE role = 'admin'
        """, (
            'nanda_admin',
            'crm@nandamac.cloud',
            password_hash,
            '11999990000',
            'active',
            'admin'
        ))
        conn.commit()
        print("   ✅ Admin Nanda criada")

        # =====================================================
        # 2. CRIAR 3 MENTORES
        # =====================================================
        print("\n🎓 Criando 3 Mentores...")

        mentores = [
            ('mentor_ana', 'ana@mentor.com', 'Ana Silva', '11999991111'),
            ('mentor_carlos', 'carlos@mentor.com', 'Carlos Santos', '11999992222'),
            ('mentor_beatriz', 'beatriz@mentor.com', 'Beatriz Costa', '11999993333'),
        ]

        mentor_ids = []
        for username, email, nome, phone in mentores:
            cursor.execute("""
                INSERT INTO users (username, email, password_hash, phone_number, account_status, role)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                username,
                email,
                password_hash,
                phone,
                'active',
                'mentor'
            ))
            conn.commit()
            mentor_id = cursor.lastrowid
            mentor_ids.append(mentor_id)
            print(f"   ✅ Mentor {nome} criado (ID: {mentor_id})")

            # Criar código de convite para o mentor (máx 20 chars)
            import random
            import string
            invite_code = f"M{mentor_id:02d}{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
            cursor.execute("""
                INSERT INTO mentor_invites (mentor_id, invite_code, is_active)
                VALUES (%s, %s, %s)
            """, (mentor_id, invite_code, True))
            conn.commit()
            print(f"      🎫 Código de convite: {invite_code}")

        # =====================================================
        # 3. CRIAR 9 MENTORADOS (3 por mentor)
        # =====================================================
        print("\n👥 Criando 9 Mentorados...")

        profissoes = ['Médico', 'Dentista', 'Fisioterapeuta']
        especialidades = [
            ['Cardiologia', 'Dermatologia', 'Ortopedia'],
            ['Ortodontia', 'Implantodontia', 'Endodontia'],
            ['Ortopedia', 'Neurologia', 'Esportiva']
        ]

        mentorado_count = 1
        for i, mentor_id in enumerate(mentor_ids):
            for j in range(3):
                profissao = profissoes[i]
                especialidade = especialidades[i][j]

                username = f'mentorado_{mentorado_count}'
                email = f'mentorado{mentorado_count}@teste.com'
                nome = f'Dr(a) Mentorado {mentorado_count}'
                phone = f'1199999{4000 + mentorado_count}'

                # Criar usuário mentorado
                cursor.execute("""
                    INSERT INTO users (username, email, password_hash, phone_number, account_status, role, mentor_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    username,
                    email,
                    password_hash,
                    phone,
                    'active',
                    'mentorado',
                    mentor_id
                ))
                conn.commit()
                user_id = cursor.lastrowid

                # Criar perfil profissional (client)
                cursor.execute("""
                    INSERT INTO clients (
                        user_id, profession, specialty, years_experience,
                        current_revenue, desired_revenue, main_challenge,
                        has_secretary, team_size, city, state
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    user_id,
                    profissao,
                    especialidade,
                    5 + j * 2,  # 5, 7, 9 anos
                    15000 + (j * 5000),  # 15k, 20k, 25k
                    50000 + (j * 10000),  # 50k, 60k, 70k
                    f'Preciso aumentar meu faturamento e atrair mais pacientes {profissao.lower()}s',
                    j > 0,  # Primeiro não tem secretária
                    1 + j,  # 1, 2, 3 pessoas
                    'São Paulo',
                    'SP'
                ))
                conn.commit()

                print(f"   ✅ Mentorado {mentorado_count} - {nome} ({profissao} - {especialidade})")
                print(f"      Vinculado ao Mentor ID: {mentor_id}")

                mentorado_count += 1

        # =====================================================
        # 4. RESUMO
        # =====================================================
        print("\n📊 Resumo da População:")
        print("=" * 60)

        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        print(f"   👑 Admins: {cursor.fetchone()[0]}")

        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'mentor'")
        print(f"   🎓 Mentores: {cursor.fetchone()[0]}")

        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'mentorado'")
        print(f"   👥 Mentorados: {cursor.fetchone()[0]}")

        cursor.execute("SELECT COUNT(*) FROM clients")
        print(f"   📋 Perfis Profissionais: {cursor.fetchone()[0]}")

        cursor.execute("SELECT COUNT(*) FROM mentor_invites")
        print(f"   🎫 Códigos de Convite: {cursor.fetchone()[0]}")

        print("\n🔑 Credenciais de Teste:")
        print("=" * 60)
        print("   👑 Admin:")
        print("      Email: crm@nandamac.cloud")
        print("      Senha: password123")
        print()
        print("   🎓 Mentores:")
        print("      Email: ana@mentor.com | Senha: password123")
        print("      Email: carlos@mentor.com | Senha: password123")
        print("      Email: beatriz@mentor.com | Senha: password123")
        print()
        print("   👥 Mentorados:")
        print("      Email: mentorado1@teste.com até mentorado9@teste.com")
        print("      Senha: password123 (para todos)")
        print("=" * 60)

        cursor.close()
        conn.close()

        print("\n✅ População concluída com sucesso!")

    except sqlite.connector.Error as err:
        print(f"\n❌ Erro ao popular banco: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
