#!/usr/bin/env python3
"""
Script para popular o banco de dados com dados realistas
"""
import sqlite.connector
import os
import sys
from datetime import datetime, timedelta
import random

# Configuração do banco de dados
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'crm'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', '')
}

# Dados realistas
WASTE_TYPES = [
    'Plástico', 'Metal', 'Vidro', 'Papel/Papelão',
    'Orgânico', 'Eletrônico', 'Entulho', 'Óleo'
]

# Localizações em São Paulo
LOCATIONS = [
    (-23.5505, -46.6333, 'Praça da Sé, Centro'),
    (-23.5615, -46.6562, 'Avenida Paulista'),
    (-23.5475, -46.6361, 'Parque Ibirapuera'),
    (-23.5329, -46.6395, 'Vila Madalena'),
    (-23.5506, -46.6342, 'Centro Histórico'),
    (-23.5875, -46.6584, 'Morumbi'),
    (-23.5204, -46.6017, 'Tatuapé'),
    (-23.6345, -46.7034, 'Santo Amaro'),
    (-23.5432, -46.6371, 'Bela Vista'),
    (-23.5558, -46.6396, 'Consolação'),
]

DESCRIPTIONS = [
    'Acúmulo de lixo na calçada há vários dias',
    'Descarte irregular de resíduos de construção',
    'Lixeira transbordando, precisa de coleta urgente',
    'Entulho abandonado em área pública',
    'Sacolas plásticas espalhadas pela rua',
    'Resíduos eletrônicos descartados incorretamente',
    'Grande volume de lixo orgânico sem coleta',
    'Móveis velhos abandonados na via pública',
    'Ponto de descarte clandestino identificado',
    'Contentor de lixo danificado',
]

def main():
    try:
        # Conectar ao banco
        print("Conectando ao banco de dados...")
        conn = sqlite.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("✅ Conectado!")

        # Verificar se usuário testuser existe
        cursor.execute("SELECT user_id FROM users WHERE username = 'testuser'")
        result = cursor.fetchone()
        if not result:
            print("❌ Usuário 'testuser' não encontrado. Crie-o primeiro.")
            return

        user_id = result[0]
        print(f"✅ Usuário testuser encontrado (ID: {user_id})")

        # Limpar dados antigos (opcional)
        print("\n🗑️  Limpando dados antigos...")
        cursor.execute("DELETE FROM analysis_results WHERE report_id IN (SELECT report_id FROM reports WHERE user_id = %s)", (user_id,))
        cursor.execute("DELETE FROM reports WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM hotspots")
        conn.commit()
        print("✅ Dados antigos removidos")

        # Inserir relatórios
        print("\n📝 Inserindo 30 relatórios...")
        report_ids = []
        for i in range(30):
            lat, lon, location_name = random.choice(LOCATIONS)
            # Adicionar pequena variação nas coordenadas
            lat += random.uniform(-0.01, 0.01)
            lon += random.uniform(-0.01, 0.01)

            waste_type = random.choice(WASTE_TYPES)
            description = random.choice(DESCRIPTIONS)
            severity = random.randint(1, 5)
            status_choices = ['pending'] * 3 + ['verified'] * 2 + ['in_progress'] * 2 + ['resolved'] * 1
            status = random.choice(status_choices)

            # Data aleatória nos últimos 30 dias
            days_ago = random.randint(0, 30)
            hours_ago = random.randint(0, 23)
            created_at = datetime.now() - timedelta(days=days_ago, hours=hours_ago)

            cursor.execute("""
                INSERT INTO reports
                (user_id, latitude, longitude, address, description, status, severity, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id, lat, lon, location_name, description,
                status, severity, created_at, created_at
            ))

            report_id = cursor.lastrowid
            report_ids.append((report_id, waste_type, severity, created_at))

            if (i + 1) % 10 == 0:
                print(f"  ✓ {i + 1} relatórios inseridos...")

        print(f"✅ {len(report_ids)} relatórios inseridos!")

        # Adicionar análises de IA para 80% dos relatórios
        print("\n🤖 Adicionando análises de IA...")
        analysis_count = 0
        for report_id, waste_type, severity, created_at in report_ids:
            if random.random() > 0.2:  # 80% chance
                confidence = random.uniform(0.75, 0.98)
                analysis_data = {
                    "detected_items": [waste_type.lower()],
                    "model": "bedrock-nova-pro",
                    "processing_time": random.uniform(0.5, 2.5)
                }

                cursor.execute("""
                    INSERT INTO analysis_results
                    (report_id, waste_type, confidence, severity, analysis_data, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    report_id, waste_type, confidence,
                    severity, str(analysis_data), created_at
                ))
                analysis_count += 1

        print(f"✅ {analysis_count} análises de IA criadas!")

        # Criar hotspots
        print("\n🔥 Criando 10 hotspots...")
        for i, (lat, lon, location_name) in enumerate(LOCATIONS):
            total_reports = random.randint(5, 20)
            avg_severity = random.uniform(2.5, 4.8)
            status_choices = ['active'] * 5 + ['monitoring'] * 3 + ['resolved'] * 2
            status = random.choice(status_choices)
            radius = random.randint(150, 500)

            days_since_created = random.randint(5, 90)
            days_since_last_report = random.randint(0, 10)

            cursor.execute("""
                INSERT INTO hotspots
                (name, center_latitude, center_longitude, radius_meters,
                 total_reports, average_severity, status, created_at, last_reported)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                f"Hotspot {location_name.split(',')[0]}", lat, lon, radius,
                total_reports, avg_severity, status,
                datetime.now() - timedelta(days=days_since_created),
                datetime.now() - timedelta(days=days_since_last_report)
            ))

        print(f"✅ 10 hotspots criados!")

        # Commit final
        conn.commit()
        print("\n💾 Dados salvos no banco!")

        # Mostrar estatísticas finais
        print("\n" + "="*50)
        print("📊 ESTATÍSTICAS DO BANCO DE DADOS")
        print("="*50)

        cursor.execute("SELECT COUNT(*) FROM reports")
        total_reports = cursor.fetchone()[0]
        print(f"📝 Total de relatórios: {total_reports}")

        cursor.execute("SELECT COUNT(*) FROM reports WHERE status = 'pending'")
        pending = cursor.fetchone()[0]
        print(f"   - Pendentes: {pending}")

        cursor.execute("SELECT COUNT(*) FROM reports WHERE status = 'verified'")
        verified = cursor.fetchone()[0]
        print(f"   - Verificados: {verified}")

        cursor.execute("SELECT COUNT(*) FROM reports WHERE status = 'in_progress'")
        in_progress = cursor.fetchone()[0]
        print(f"   - Em progresso: {in_progress}")

        cursor.execute("SELECT COUNT(*) FROM reports WHERE status = 'resolved'")
        resolved = cursor.fetchone()[0]
        print(f"   - Resolvidos: {resolved}")

        cursor.execute("SELECT COUNT(*) FROM analysis_results")
        total_analysis = cursor.fetchone()[0]
        print(f"\n🤖 Análises de IA: {total_analysis}")

        cursor.execute("SELECT COUNT(*) FROM hotspots")
        total_hotspots = cursor.fetchone()[0]
        print(f"🔥 Total de hotspots: {total_hotspots}")

        cursor.execute("SELECT COUNT(*) FROM hotspots WHERE status = 'active'")
        active_hotspots = cursor.fetchone()[0]
        print(f"   - Ativos: {active_hotspots}")

        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        print(f"\n👥 Total de usuários: {total_users}")

        print("="*50)
        print("\n🎉 Banco de dados populado com sucesso!")
        print("🌐 Recarregue o dashboard em: http://localhost:4200/dashboard")

        cursor.close()
        conn.close()

    except sqlite.connector.Error as err:
        print(f"\n❌ Erro de banco de dados: {err}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Erro: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
