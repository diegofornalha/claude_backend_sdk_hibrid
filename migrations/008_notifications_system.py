"""
Migration 008: Notifications and Presence System

Adiciona:
- Tabela notifications (histórico de notificações)
- Tabela user_presence (rastreamento de presença online)
- Tabela session_watchers (admins observando sessões)
"""


def up(conn):
    """Apply migration"""
    cursor = conn.cursor()

    # ===== NOTIFICAÇÕES =====
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_user_id INTEGER NOT NULL,
            from_user_id INTEGER,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            priority TEXT DEFAULT 'medium' CHECK(priority IN ('high', 'medium', 'low')),
            action_url TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            read_at TEXT,
            FOREIGN KEY (target_user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (from_user_id) REFERENCES users(user_id) ON DELETE SET NULL
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_target ON notifications(target_user_id, is_read)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_priority ON notifications(priority, is_read)")

    # ===== PRESENÇA ONLINE =====
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_presence (
            user_id INTEGER PRIMARY KEY,
            is_online INTEGER DEFAULT 1,
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP,
            connected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            connection_count INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_presence_online ON user_presence(is_online)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_presence_last_seen ON user_presence(last_seen)")

    # ===== WATCHERS (admins observando sessões) =====
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_watchers (
            session_id TEXT NOT NULL,
            admin_user_id INTEGER NOT NULL,
            started_watching_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (session_id, admin_user_id),
            FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
            FOREIGN KEY (admin_user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchers_session ON session_watchers(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchers_admin ON session_watchers(admin_user_id)")

    conn.commit()
    print("✅ Migration 008 applied: Notifications and Presence System")


def down(conn):
    """Revert migration"""
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS notifications")
    cursor.execute("DROP TABLE IF EXISTS user_presence")
    cursor.execute("DROP TABLE IF EXISTS session_watchers")
    conn.commit()
    print("✅ Migration 008 reverted")
