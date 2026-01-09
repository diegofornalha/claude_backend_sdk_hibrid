#!/usr/bin/env python3
"""
Script para resetar o banco de dados e criar apenas o admin do CRM Web.

ATENÇÃO: Este script irá:
1. Fazer backup do banco atual
2. Deletar o banco existente
3. Criar banco novo limpo
4. Executar todas as migrações
5. Criar apenas o usuário admin especificado

Email: claude_front_sdk_crm@admin.com
Senha: claude_front_sdk_crm321
"""

import os
import sys
import sqlite3
import hashlib
import shutil
from datetime import datetime

# Path do banco
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'crm.db')
BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backups')

# Dados do usuário admin
EMAIL = "claude_front_sdk_crm@admin.com"
USERNAME = "crm-admin"
PASSWORD = "claude_front_sdk_crm321"
ADMIN_LEVEL = 0  # Dono (controle total)
ROLE = "admin"


def hash_password(password: str, salt: str = None) -> str:
    """Hash de senha usando SHA-256."""
    if salt is None:
        import random
        import string
        salt = ''.join(random.choices(string.ascii_letters + string.digits, k=16))

    salted_password = salt + password
    hashed = hashlib.sha256(salted_password.encode()).hexdigest()
    return f"{salt}${hashed}"


def backup_database():
    """Faz backup do banco atual."""
    if not os.path.exists(DB_PATH):
        print("⚠️  Banco de dados não existe. Pulando backup.")
        return None

    # Criar diretório de backups
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # Nome do backup com timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(BACKUP_DIR, f'nanda_backup_{timestamp}.db')

    print(f"📦 Fazendo backup do banco atual...")
    shutil.copy2(DB_PATH, backup_path)
    print(f"   ✅ Backup salvo em: {backup_path}")

    return backup_path


def delete_database():
    """Remove o banco de dados existente."""
    if os.path.exists(DB_PATH):
        print("🗑️  Removendo banco existente...")
        os.remove(DB_PATH)
        print("   ✅ Banco removido")
    else:
        print("⚠️  Banco não existe")


def run_migrations():
    """Executa todas as migrações."""
    migrations_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations')

    # Lista de migrações na ordem
    migrations = [
        '002_database_improvements.py',
        '004_tenant_config.py',
        '005_agnostic_config.py',
        '006_evolution_stages.py',
        '007_admin_levels.py',
    ]

    print("\n🔧 Executando migrações...")

    sys.path.insert(0, migrations_dir)

    for migration_file in migrations:
        migration_path = os.path.join(migrations_dir, migration_file)

        if not os.path.exists(migration_path):
            print(f"   ⚠️  {migration_file} não encontrado. Pulando...")
            continue

        print(f"\n   📋 Executando {migration_file}...")

        try:
            # Importar e executar a migração
            module_name = migration_file.replace('.py', '')

            # Limpar módulo anterior se existir
            if module_name in sys.modules:
                del sys.modules[module_name]

            module = __import__(module_name)

            if hasattr(module, 'run_migration'):
                module.run_migration()
                print(f"   ✅ {migration_file} concluída")
            else:
                print(f"   ⚠️  {migration_file} não tem função run_migration()")

        except Exception as e:
            print(f"   ❌ Erro ao executar {migration_file}: {e}")
            import traceback
            traceback.print_exc()


def create_admin_user():
    """Cria o usuário admin."""
    print("\n🔐 Criando usuário admin...")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Hash da senha
    hashed_password = hash_password(PASSWORD)

    # Criar usuário
    try:
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

        print(f"   ✅ Usuário criado com sucesso!")
        print(f"      User ID: {user_id}")
        print(f"      Email: {EMAIL}")
        print(f"      Username: {USERNAME}")
        print(f"      Admin Level: {ADMIN_LEVEL} (Dono)")
        print(f"      Role: {ROLE}")

    except Exception as e:
        print(f"   ❌ Erro ao criar usuário: {e}")
        conn.rollback()

    finally:
        conn.close()


def verify_setup():
    """Verifica se tudo foi criado corretamente."""
    print("\n🔍 Verificando setup...")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Verificar usuário
    cursor = conn.execute("""
        SELECT u.user_id, u.username, u.email, u.role, u.admin_level,
               al.name as level_name, al.description as level_description
        FROM users u
        LEFT JOIN admin_levels al ON u.admin_level = al.level AND u.tenant_id = al.tenant_id
        WHERE u.email = ?
    """, (EMAIL,))

    result = cursor.fetchone()

    if result:
        print("   ✅ Usuário encontrado:")
        print(f"      User ID: {result['user_id']}")
        print(f"      Username: {result['username']}")
        print(f"      Email: {result['email']}")
        print(f"      Role: {result['role']}")
        print(f"      Admin Level: {result['admin_level']} - {result['level_name']}")
        print(f"      Descrição: {result['level_description']}")
    else:
        print("   ❌ Usuário não encontrado!")

    # Contar total de usuários
    cursor = conn.execute("SELECT COUNT(*) as total FROM users")
    total_users = cursor.fetchone()['total']
    print(f"\n   📊 Total de usuários no banco: {total_users}")

    conn.close()


def main():
    """Função principal."""
    print("=" * 70)
    print("🔄 RESET DO BANCO DE DADOS - CRM WEB")
    print("=" * 70)
    print("\n⚠️  ATENÇÃO: Esta operação irá:")
    print("   - Fazer backup do banco atual")
    print("   - Deletar o banco existente")
    print("   - Criar banco novo limpo")
    print("   - Executar todas as migrações")
    print("   - Criar apenas 1 usuário admin")
    print("\n" + "=" * 70)

    # Confirmar (aceita --force ou -f como argumento)
    if len(sys.argv) > 1 and sys.argv[1] in ['--force', '-f', '--yes', '-y']:
        print("\n✅ Confirmação automática (--force)")
    else:
        try:
            response = input("\n❓ Tem certeza que deseja continuar? (sim/não): ").strip().lower()
            if response not in ['sim', 's', 'yes', 'y']:
                print("\n❌ Operação cancelada.")
                return
        except EOFError:
            print("\n⚠️  Modo não-interativo detectado. Use --force para confirmar automaticamente.")
            print("❌ Operação cancelada.")
            return

    print("\n🚀 Iniciando reset...\n")

    # 1. Backup
    backup_path = backup_database()

    # 2. Deletar banco
    delete_database()

    # 3. Executar migrações (isso criará o banco novo)
    run_migrations()

    # 4. Criar admin
    create_admin_user()

    # 5. Verificar
    verify_setup()

    print("\n" + "=" * 70)
    print("✅ RESET CONCLUÍDO COM SUCESSO!")
    print("=" * 70)
    print("\n📋 Credenciais de acesso:")
    print(f"   Email: {EMAIL}")
    print(f"   Senha: {PASSWORD}")

    if backup_path:
        print(f"\n💾 Backup anterior salvo em:")
        print(f"   {backup_path}")

    print("\n⚠️  IMPORTANTE: Guarde esta senha em local seguro!")
    print("=" * 70)


if __name__ == "__main__":
    main()
