"""
Migration 010: Tornar email e password_hash nullable

Sistema passwordless — novos usuários não têm email nem senha.
SQLite não suporta ALTER COLUMN, então recriamos a tabela.
"""


def up(conn):
    """Apply migration"""
    cursor = conn.cursor()

    # Desabilitar FK checks temporariamente
    cursor.execute("PRAGMA foreign_keys = OFF")

    # 1. Criar tabela nova sem NOT NULL em email/password_hash
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users_new (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT,
            password_hash TEXT,
            phone_number TEXT,
            registration_date TEXT DEFAULT CURRENT_TIMESTAMP,
            last_login TEXT,
            account_status TEXT DEFAULT 'active',
            profile_image_url TEXT,
            verification_status INTEGER DEFAULT 0,
            role TEXT DEFAULT 'mentorado',
            profession TEXT,
            specialty TEXT,
            current_revenue REAL,
            desired_revenue REAL,
            deleted_at DATETIME,
            tenant_id TEXT DEFAULT 'default',
            current_stage_key TEXT DEFAULT 'lead',
            stage_history TEXT,
            promoted_to_tenant_id TEXT,
            admin_level INTEGER DEFAULT NULL
        )
    """)

    # 2. Copiar dados — converter strings vazias para NULL
    cursor.execute("""
        INSERT INTO users_new
        SELECT
            user_id, username,
            CASE WHEN email = '' THEN NULL ELSE email END,
            CASE WHEN password_hash = '' THEN NULL ELSE password_hash END,
            phone_number, registration_date, last_login, account_status,
            profile_image_url, verification_status, role, profession,
            specialty, current_revenue, desired_revenue, deleted_at,
            tenant_id, current_stage_key, stage_history,
            promoted_to_tenant_id, admin_level
        FROM users
    """)

    # 3. Dropar tabela antiga e renomear
    cursor.execute("DROP TABLE users")
    cursor.execute("ALTER TABLE users_new RENAME TO users")

    # 4. Recriar índices
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_stage ON users(current_stage_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_admin_level ON users(admin_level)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_status ON users(account_status)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique ON users(phone_number)")

    # 5. Reabilitar FK checks
    cursor.execute("PRAGMA foreign_keys = ON")

    conn.commit()
    print("  [OK] Migration 010: email e password_hash agora são nullable")


def down(conn):
    """Rollback — não necessário (nullable é seguro)"""
    print("  [SKIP] Migration 010 rollback: não necessário")
