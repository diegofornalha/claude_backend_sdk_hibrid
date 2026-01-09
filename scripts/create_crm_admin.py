#!/usr/bin/env python3
"""
Script para criar/atualizar usuário admin do CRM Web
Email: claude_front_sdk_crm@admin.com
Senha: claude_front_sdk_crm321
Nível: 0 (Dono - controle total)
"""

import os
import sys
import sqlite3
import hashlib


def hash_password(password: str, salt: str = None) -> str:
    """
    Hash de senha usando SHA-256.
    Compatível com o sistema de auth do backend.
    """
    if salt is None:
        # Gerar salt aleatório
        import random
        import string
        salt = ''.join(random.choices(string.ascii_letters + string.digits, k=16))

    # Combinar salt + senha e fazer hash
    salted_password = salt + password
    hashed = hashlib.sha256(salted_password.encode()).hexdigest()

    # Retornar no formato: salt$hash
    return f"{salt}${hashed}"

# Path do banco
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'crm.db')

# Dados do usuário
EMAIL = "claude_front_sdk_crm@admin.com"
USERNAME = "crm-admin"
PASSWORD = "claude_front_sdk_crm321"
ADMIN_LEVEL = 0  # Dono (controle total)
ROLE = "admin"


def create_or_update_admin():
    """Cria ou atualiza o usuário admin do CRM."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print("🔐 Criando/atualizando usuário admin do CRM Web")
    print("=" * 60)
    print(f"Email: {EMAIL}")
    print(f"Username: {USERNAME}")
    print(f"Admin Level: {ADMIN_LEVEL} (Dono)")
    print("=" * 60)

    # Verificar se usuário já existe
    cursor = conn.execute("SELECT user_id, username, email, admin_level FROM users WHERE email = ?", (EMAIL,))
    existing_user = cursor.fetchone()

    # Hash da senha
    hashed_password = hash_password(PASSWORD)

    if existing_user:
        print(f"\n✓ Usuário já existe (ID: {existing_user['user_id']})")
        print("  Atualizando senha e admin_level...")

        # Atualizar usuário existente
        conn.execute("""
            UPDATE users
            SET password_hash = ?,
                admin_level = ?,
                role = ?,
                username = ?,
                tenant_id = 'default',
                account_status = 'active'
            WHERE email = ?
        """, (hashed_password, ADMIN_LEVEL, ROLE, USERNAME, EMAIL))

        conn.commit()
        print(f"  ✅ Usuário atualizado com sucesso!")
        print(f"     - Nova senha definida")
        print(f"     - Admin Level: {ADMIN_LEVEL} (Dono)")
        print(f"     - Role: {ROLE}")

    else:
        print("\n✓ Criando novo usuário...")

        # Criar novo usuário
        cursor = conn.execute("""
            INSERT INTO users (
                username,
                email,
                password_hash,
                role,
                admin_level,
                tenant_id,
                registration_date,
                account_status,
                verification_status
            ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), 'active', 1)
        """, (USERNAME, EMAIL, hashed_password, ROLE, ADMIN_LEVEL, 'default'))

        conn.commit()
        user_id = cursor.lastrowid

        print(f"  ✅ Usuário criado com sucesso (ID: {user_id})!")
        print(f"     - Email: {EMAIL}")
        print(f"     - Username: {USERNAME}")
        print(f"     - Admin Level: {ADMIN_LEVEL} (Dono)")
        print(f"     - Role: {ROLE}")

    # Verificar o resultado final
    cursor = conn.execute("""
        SELECT u.user_id, u.username, u.email, u.role, u.admin_level,
               al.name as level_name, al.description as level_description
        FROM users u
        LEFT JOIN admin_levels al ON u.admin_level = al.level AND u.tenant_id = al.tenant_id
        WHERE u.email = ?
    """, (EMAIL,))

    result = cursor.fetchone()

    print("\n" + "=" * 60)
    print("📊 RESUMO FINAL")
    print("=" * 60)
    print(f"User ID:      {result['user_id']}")
    print(f"Username:     {result['username']}")
    print(f"Email:        {result['email']}")
    print(f"Role:         {result['role']}")
    print(f"Admin Level:  {result['admin_level']} - {result['level_name']}")
    print(f"Descrição:    {result['level_description']}")
    print("=" * 60)
    print("\n✅ Pronto! Você pode fazer login com:")
    print(f"   Email: {EMAIL}")
    print(f"   Senha: {PASSWORD}")
    print("\n⚠️  IMPORTANTE: Guarde esta senha em local seguro e considere alterá-la após o primeiro login.")

    conn.close()


if __name__ == "__main__":
    create_or_update_admin()
