#!/usr/bin/env python3
"""
Script para criar 6 usuarios ficticios de teste - um para cada nivel.

Clientes v4 - Usuarios de Teste:
- Level 0: Proprietario - acesso total
- Level 1: Admin - administrador
- Level 2: Mentor Senior - mentor com permissoes elevadas
- Level 3: Mentor - mentor padrao
- Level 4: Mentorado - usuario mentorado
- Level 5: Lead - lead/prospect

Executar: python scripts/seed_test_users.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hashlib
from datetime import datetime
from core.turso_database import get_db_connection

def hash_password(password: str) -> str:
    """Hash password using SHA256."""
    return hashlib.sha256(password.encode()).hexdigest()

def main():
    """Cria 6 usuarios ficticios de teste."""

    # Usuarios ficticios para cada nivel
    test_users = [
        {
            "level": 0,
            "username": "proprietario_teste",
            "email": "proprietario@teste.com",
            "phone_number": "11999990000",
            "profession": "CEO",
            "specialty": "Gestao Empresarial",
            "role": "admin",
            "account_status": "active",
        },
        {
            "level": 1,
            "username": "admin_teste",
            "email": "admin@teste.com",
            "phone_number": "11999991111",
            "profession": "Administrador",
            "specialty": "Sistemas",
            "role": "admin",
            "account_status": "active",
        },
        {
            "level": 2,
            "username": "mentor_senior_teste",
            "email": "mentor.senior@teste.com",
            "phone_number": "11999992222",
            "profession": "Medico",
            "specialty": "Cardiologia",
            "role": "mentor",
            "account_status": "active",
        },
        {
            "level": 3,
            "username": "mentor_teste",
            "email": "mentor@teste.com",
            "phone_number": "11999993333",
            "profession": "Dentista",
            "specialty": "Ortodontia",
            "role": "mentor",
            "account_status": "active",
        },
        {
            "level": 4,
            "username": "mentorado_teste",
            "email": "mentorado@teste.com",
            "phone_number": "11999994444",
            "profession": "Fisioterapeuta",
            "specialty": "Ortopedia",
            "role": "mentorado",
            "account_status": "mentorado",
            "current_revenue": "8234.00",
            "desired_revenue": "25000.00",
        },
        {
            "level": 5,
            "username": "lead_teste",
            "email": "lead@teste.com",
            "phone_number": "11999995555",
            "profession": "Nutricionista",
            "specialty": "Esportiva",
            "role": "lead",
            "account_status": "lead",
        },
    ]

    password_hash = hash_password("teste123")

    conn = get_db_connection()
    if not conn:
        print("Erro ao conectar ao banco de dados")
        return

    try:
        cursor = conn.cursor()

        print("\n" + "="*60)
        print("SEED: Criando 6 usuarios ficticios de teste")
        print("="*60 + "\n")

        created_count = 0
        skipped_count = 0

        for user in test_users:
            # Verificar se ja existe
            cursor.execute("SELECT user_id FROM users WHERE email = ?", (user["email"],))
            existing = cursor.fetchone()

            if existing:
                print(f"  [SKIP] Level {user['level']}: {user['username']} ({user['email']}) - ja existe")
                skipped_count += 1
                continue

            # Inserir usuario
            cursor.execute("""
                INSERT INTO users (
                    username, email, password_hash, phone_number,
                    profession, specialty, current_revenue, desired_revenue,
                    role, account_status, admin_level, registration_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user["username"],
                user["email"],
                password_hash,
                user["phone_number"],
                user.get("profession"),
                user.get("specialty"),
                user.get("current_revenue"),
                user.get("desired_revenue"),
                user["role"],
                user["account_status"],
                user["level"],
                datetime.now().isoformat(),
            ))

            print(f"  [OK] Level {user['level']}: {user['username']} ({user['email']})")
            created_count += 1

        conn.commit()

        print("\n" + "-"*60)
        print(f"Resultado: {created_count} criados, {skipped_count} ja existiam")
        print("-"*60)

        # Listar todos os usuarios por nivel
        print("\n📊 Usuarios por nivel:")
        for level in range(6):
            cursor.execute("""
                SELECT COUNT(*) as count FROM users
                WHERE admin_level = ? OR (admin_level IS NULL AND role = ?)
            """, (level, ["admin", "admin", "mentor", "mentor", "mentorado", "lead"][level]))
            count = cursor.fetchone()
            level_names = ["Proprietario", "Admin", "Mentor Senior", "Mentor", "Mentorado", "Lead"]
            print(f"   Level {level} ({level_names[level]}): {count[0] if count else 0} usuarios")

        print("\n✅ Seed concluido!")
        print("   Senha de todos: teste123")
        print("")

    except Exception as e:
        print(f"\n❌ Erro: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
