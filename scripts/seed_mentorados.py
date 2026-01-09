#!/usr/bin/env python3
"""
Seed Mentorados - Popula o banco com mentorados de teste

Uso:
    python scripts/seed_mentorados.py

Cria mentorados com diferentes perfis para testes de desenvolvimento.
"""

import sys
import os
from pathlib import Path

# Adiciona o diretório raiz ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
from core.auth import hash_password

# Configuração do banco
DB_PATH = Path(__file__).parent.parent / "crm.db"

# Mentorados de teste
MENTORADOS_SEED = [
    {
        "username": "Maria Silva",
        "email": "maria.silva@exemplo.com",
        "password": "senha123",
        "phone_number": "+5511999001001",
        "profession": "Nutricionista",
        "specialty": "Nutrição Esportiva",
        "current_revenue": 8234.00,
        "desired_revenue": 25000.00,
    },
    {
        "username": "João Santos",
        "email": "joao.santos@exemplo.com",
        "password": "senha123",
        "phone_number": "+5511999002002",
        "profession": "Personal Trainer",
        "specialty": "Emagrecimento",
        "current_revenue": 5000.00,
        "desired_revenue": 15000.00,
    },
    {
        "username": "Ana Oliveira",
        "email": "ana.oliveira@exemplo.com",
        "password": "senha123",
        "phone_number": "+5521999003003",
        "profession": "Psicóloga",
        "specialty": "Terapia Cognitivo-Comportamental",
        "current_revenue": 12000.00,
        "desired_revenue": 30000.00,
    },
    {
        "username": "Carlos Ferreira",
        "email": "carlos.ferreira@exemplo.com",
        "password": "senha123",
        "phone_number": "+5531999004004",
        "profession": "Coach",
        "specialty": "Coach de Carreira",
        "current_revenue": 3000.00,
        "desired_revenue": 20000.00,
    },
    {
        "username": "Patrícia Lima",
        "email": "patricia.lima@exemplo.com",
        "password": "senha123",
        "phone_number": "+5511999005005",
        "profession": "Fisioterapeuta",
        "specialty": "Pilates",
        "current_revenue": 7000.00,
        "desired_revenue": 18000.00,
    },
    {
        "username": "Roberto Costa",
        "email": "roberto.costa@exemplo.com",
        "password": "senha123",
        "phone_number": "+5541999006006",
        "profession": "Médico",
        "specialty": "Medicina Integrativa",
        "current_revenue": 20000.00,
        "desired_revenue": 50000.00,
    },
    {
        "username": "Fernanda Almeida",
        "email": "fernanda.almeida@exemplo.com",
        "password": "senha123",
        "phone_number": "+5551999007007",
        "profession": "Dentista",
        "specialty": "Harmonização Facial",
        "current_revenue": 15000.00,
        "desired_revenue": 40000.00,
    },
    {
        "username": "Lucas Martins",
        "email": "lucas.martins@exemplo.com",
        "password": "senha123",
        "phone_number": "+5561999008008",
        "profession": "Advogado",
        "specialty": "Direito Digital",
        "current_revenue": 10000.00,
        "desired_revenue": 35000.00,
    },
    {
        "username": "Juliana Pereira",
        "email": "juliana.pereira@exemplo.com",
        "password": "senha123",
        "phone_number": "+5571999009009",
        "profession": "Arquiteta",
        "specialty": "Design de Interiores",
        "current_revenue": 6000.00,
        "desired_revenue": 22000.00,
    },
    {
        "username": "Marcos Ribeiro",
        "email": "marcos.ribeiro@exemplo.com",
        "password": "senha123",
        "phone_number": "+5581999010010",
        "profession": "Contador",
        "specialty": "Contabilidade para Infoprodutores",
        "current_revenue": 9000.00,
        "desired_revenue": 28000.00,
    },
]


def run_seed():
    """Executa o seed de mentorados"""
    try:
        import libsql_experimental as libsql
    except ImportError:
        print("❌ libsql_experimental não instalado. Tentando sqlite3...")
        import sqlite3 as libsql

    print("🌱 Seed de Mentorados")
    print("=" * 50)

    # Conectar ao banco
    try:
        conn = libsql.connect(str(DB_PATH))
        print(f"✅ Conectado ao banco: {DB_PATH}")
    except Exception as e:
        print(f"❌ Erro ao conectar: {e}")
        return

    cursor = conn.cursor()

    # Verificar mentorados existentes
    cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'mentorado'")
    existing_count = cursor.fetchone()[0]
    print(f"📊 Mentorados existentes: {existing_count}")

    if existing_count > 0:
        response = input("\n⚠️  Já existem mentorados. Deseja adicionar mais? (s/n): ")
        if response.lower() != 's':
            print("Operação cancelada.")
            conn.close()
            return

    # Inserir mentorados
    inserted = 0
    skipped = 0

    for mentorado in MENTORADOS_SEED:
        # Verificar se email já existe
        cursor.execute("SELECT user_id FROM users WHERE email = ?", (mentorado["email"],))
        if cursor.fetchone():
            print(f"  ⏭️  {mentorado['username']} ({mentorado['email']}) - já existe")
            skipped += 1
            continue

        # Hash da senha
        password_hash = hash_password(mentorado["password"])

        # Inserir usuário
        cursor.execute("""
            INSERT INTO users (
                username, email, password_hash, phone_number,
                profession, specialty, current_revenue, desired_revenue,
                role, account_status, registration_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'mentorado', 'active', datetime('now'))
        """, (
            mentorado["username"],
            mentorado["email"],
            password_hash,
            mentorado["phone_number"],
            mentorado["profession"],
            mentorado["specialty"],
            mentorado["current_revenue"],
            mentorado["desired_revenue"],
        ))

        print(f"  ✅ {mentorado['username']} ({mentorado['profession']})")
        inserted += 1

    conn.commit()
    conn.close()

    print("\n" + "=" * 50)
    print(f"✅ Seed concluído!")
    print(f"   - Inseridos: {inserted}")
    print(f"   - Pulados (já existiam): {skipped}")

    # Mostrar estatísticas finais
    conn = libsql.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT role, COUNT(*) FROM users GROUP BY role")
    stats = cursor.fetchall()
    conn.close()

    print("\n📊 Estatísticas do banco:")
    for role, count in stats:
        print(f"   - {role}: {count}")


if __name__ == "__main__":
    run_seed()
