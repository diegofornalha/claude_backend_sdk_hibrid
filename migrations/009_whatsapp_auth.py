"""
Migration 009: WhatsApp Auth (Passwordless OTP)

Cria/atualiza tabelas para autenticação via WhatsApp OTP:
- pending_registrations (com phone_number e role)
- user_verifications (com phone_number)
- Índice UNIQUE em users.phone_number
"""


def up(conn):
    """Apply migration"""
    cursor = conn.cursor()

    # ===== Criar tabela pending_registrations se não existe =====
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_registrations (
            registration_id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT,
            email TEXT,
            username TEXT,
            password_hash TEXT,
            otp TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempts INTEGER DEFAULT 0,
            role TEXT DEFAULT 'mentorado',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ===== Criar tabela user_verifications se não existe =====
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_verifications (
            verification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            phone_number TEXT,
            email TEXT,
            otp TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            attempts INTEGER DEFAULT 0,
            is_verified INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)

    # ===== Adicionar colunas extras se faltam (tabelas pré-existentes) =====
    for col_sql in [
        "ALTER TABLE pending_registrations ADD COLUMN phone_number TEXT",
        "ALTER TABLE pending_registrations ADD COLUMN role TEXT DEFAULT 'mentorado'",
        "ALTER TABLE user_verifications ADD COLUMN phone_number TEXT",
    ]:
        try:
            cursor.execute(col_sql)
        except Exception:
            pass  # Coluna já existe (criada no CREATE TABLE acima)

    # ===== users: índice UNIQUE em phone_number =====
    try:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique ON users(phone_number)")
    except Exception as e:
        print(f"  Aviso: Não foi possível criar índice UNIQUE em users.phone_number: {e}")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone_number)")

    # ===== Índices em phone_number =====
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_verifications_phone ON user_verifications(phone_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_phone ON pending_registrations(phone_number)")

    conn.commit()
    print("  [OK] Migration 009: WhatsApp Auth tables and indexes created")


def down(conn):
    """Rollback migration"""
    cursor = conn.cursor()

    cursor.execute("DROP INDEX IF EXISTS idx_users_phone_unique")
    cursor.execute("DROP INDEX IF EXISTS idx_users_phone")
    cursor.execute("DROP INDEX IF EXISTS idx_verifications_phone")
    cursor.execute("DROP INDEX IF EXISTS idx_pending_phone")

    conn.commit()
    print("  [OK] Migration 009 rolled back (indexes dropped)")
