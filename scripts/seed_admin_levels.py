#!/usr/bin/env python3
"""
Script para popular todos os 6 niveis na tabela admin_levels.

Niveis:
0 - Proprietario (Dono)
1 - Admin (Diretor)
2 - Mentor Senior (Gestor)
3 - Mentor
4 - Mentorado
5 - Lead

Executar: python scripts/seed_admin_levels.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from core.turso_database import get_db_connection

def main():
    """Popula tabela admin_levels com os 6 niveis."""

    levels = [
        {
            "level": 0,
            "name": "Proprietario",
            "description": "Controle total do sistema (Dono)",
            "permissions": json.dumps(["*"]),
            "can_manage_levels": "1,2,3,4,5"
        },
        {
            "level": 1,
            "name": "Admin",
            "description": "Administrador com acesso total",
            "permissions": json.dumps(["view_all", "manage_users", "reports", "config"]),
            "can_manage_levels": "2,3,4,5"
        },
        {
            "level": 2,
            "name": "Mentor Senior",
            "description": "Mentor com permissoes elevadas",
            "permissions": json.dumps(["view_team", "manage_mentors", "reports"]),
            "can_manage_levels": "3,4,5"
        },
        {
            "level": 3,
            "name": "Mentor",
            "description": "Mentor padrao - gerencia mentorados",
            "permissions": json.dumps(["view_mentees", "manage_mentees"]),
            "can_manage_levels": "4,5"
        },
        {
            "level": 4,
            "name": "Mentorado",
            "description": "Usuario mentorado/aluno",
            "permissions": json.dumps(["view_own", "chat"]),
            "can_manage_levels": ""
        },
        {
            "level": 5,
            "name": "Lead",
            "description": "Lead/prospect (potencial cliente)",
            "permissions": json.dumps(["view_public"]),
            "can_manage_levels": ""
        },
    ]

    conn = get_db_connection()
    if not conn:
        print("Erro ao conectar ao banco de dados")
        return

    try:
        cursor = conn.cursor()

        print("\n" + "="*60)
        print("SEED: Populando tabela admin_levels (6 niveis)")
        print("="*60 + "\n")

        for level_data in levels:
            # Verificar se ja existe
            cursor.execute(
                "SELECT id FROM admin_levels WHERE tenant_id = 'default' AND level = ?",
                (level_data["level"],)
            )
            existing = cursor.fetchone()

            if existing:
                # Atualizar
                cursor.execute("""
                    UPDATE admin_levels
                    SET name = ?, description = ?, permissions = ?, can_manage_levels = ?
                    WHERE tenant_id = 'default' AND level = ?
                """, (
                    level_data["name"],
                    level_data["description"],
                    level_data["permissions"],
                    level_data["can_manage_levels"],
                    level_data["level"],
                ))
                print(f"  [UPDATE] Level {level_data['level']}: {level_data['name']}")
            else:
                # Inserir
                cursor.execute("""
                    INSERT INTO admin_levels
                    (tenant_id, level, name, description, permissions, can_manage_levels)
                    VALUES ('default', ?, ?, ?, ?, ?)
                """, (
                    level_data["level"],
                    level_data["name"],
                    level_data["description"],
                    level_data["permissions"],
                    level_data["can_manage_levels"],
                ))
                print(f"  [INSERT] Level {level_data['level']}: {level_data['name']}")

        conn.commit()

        print("\n" + "-"*60)
        print("Verificando niveis cadastrados:")
        cursor.execute("SELECT level, name, description FROM admin_levels WHERE tenant_id = 'default' ORDER BY level")
        rows = cursor.fetchall()
        for row in rows:
            print(f"  Level {row[0]}: {row[1]} - {row[2]}")

        print("\n✅ Seed concluido! 6 niveis cadastrados.")

    except Exception as e:
        print(f"\n❌ Erro: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
