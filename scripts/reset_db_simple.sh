#!/bin/bash
# Script simples para resetar o banco e criar apenas o admin

set -e

DB_PATH="../crm.db"
BACKUP_DIR="../backups"
EMAIL="claude_front_sdk_crm@admin.com"
USERNAME="crm-admin"
PASSWORD="claude_front_sdk_crm321"

echo "======================================================================"
echo "🔄 RESET DO BANCO DE DADOS - CRM WEB (Versão Simples)"
echo "======================================================================"
echo ""
echo "⚠️  ATENÇÃO: Esta operação irá:"
echo "   - Fazer backup do banco atual"
echo "   - Deletar o banco existente"
echo "   - Criar banco novo limpo com estrutura mínima"
echo "   - Criar apenas 1 usuário admin"
echo ""
echo "======================================================================"
echo ""

# Criar diretório de backups
mkdir -p "$BACKUP_DIR"

# Backup
if [ -f "$DB_PATH" ]; then
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_FILE="$BACKUP_DIR/nanda_backup_$TIMESTAMP.db"
    echo "📦 Fazendo backup..."
    cp "$DB_PATH" "$BACKUP_FILE"
    echo "   ✅ Backup salvo: $BACKUP_FILE"
    echo ""
fi

# Deletar banco
if [ -f "$DB_PATH" ]; then
    echo "🗑️  Removendo banco existente..."
    rm "$DB_PATH"
    echo "   ✅ Banco removido"
    echo ""
fi

# Gerar salt e hash da senha
SALT=$(python3 -c "import random, string; print(''.join(random.choices(string.ascii_letters + string.digits, k=16)))")
SALTED_PASSWORD="$SALT$PASSWORD"
HASH=$(echo -n "$SALTED_PASSWORD" | python3 -c "import sys, hashlib; print(hashlib.sha256(sys.stdin.read().encode()).hexdigest())")
PASSWORD_HASH="$SALT\$$HASH"

echo "🔧 Criando banco novo..."

# Criar banco e popular
sqlite3 "$DB_PATH" <<EOF
-- Habilitar foreign keys
PRAGMA foreign_keys = ON;

-- =====================================================
-- TABELA: users
-- =====================================================
CREATE TABLE users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
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
);

CREATE INDEX idx_users_tenant ON users(tenant_id);
CREATE INDEX idx_users_stage ON users(current_stage_key);
CREATE INDEX idx_users_admin_level ON users(admin_level);
CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_status ON users(account_status);

-- =====================================================
-- TABELA: admin_levels
-- =====================================================
CREATE TABLE admin_levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT DEFAULT 'default',
    level INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    permissions TEXT,
    can_manage_levels TEXT,
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(tenant_id, level)
);

CREATE INDEX idx_admin_levels_tenant ON admin_levels(tenant_id);
CREATE INDEX idx_admin_levels_level ON admin_levels(level);

-- =====================================================
-- POPULAR: admin_levels
-- =====================================================
INSERT INTO admin_levels (tenant_id, level, name, description, permissions, can_manage_levels)
VALUES
    ('default', 0, 'Super Admin', 'Controle total do sistema (Dono)', '["*"]', '0,1,2,3,4,5'),
    ('default', 1, 'Diretor', 'Gerencia gestores e visualiza métricas gerais', '["view_all", "manage_users", "reports"]', '2,3,4,5'),
    ('default', 2, 'Gestor', 'Gerencia clientes e mentorados diretamente', '["view_team", "manage_clients"]', '3,4,5');

-- =====================================================
-- CRIAR USUÁRIO ADMIN
-- =====================================================
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
) VALUES (
    '$USERNAME',
    '$EMAIL',
    '$PASSWORD_HASH',
    'admin',
    0,
    'default',
    datetime('now'),
    'active',
    1
);

EOF

echo "   ✅ Banco criado com sucesso!"
echo ""

# Verificar
echo "🔍 Verificando setup..."
sqlite3 "$DB_PATH" <<EOF
SELECT
    '   User ID: ' || user_id ||
    char(10) || '   Username: ' || username ||
    char(10) || '   Email: ' || email ||
    char(10) || '   Role: ' || role ||
    char(10) || '   Admin Level: ' || admin_level
FROM users WHERE email = '$EMAIL';
EOF

TOTAL_USERS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM users;")
echo ""
echo "   📊 Total de usuários: $TOTAL_USERS"
echo ""

echo "======================================================================"
echo "✅ RESET CONCLUÍDO COM SUCESSO!"
echo "======================================================================"
echo ""
echo "📋 Credenciais de acesso:"
echo "   Email: $EMAIL"
echo "   Senha: $PASSWORD"
echo ""
if [ -f "$BACKUP_FILE" ]; then
    echo "💾 Backup anterior salvo em:"
    echo "   $BACKUP_FILE"
    echo ""
fi
echo "⚠️  IMPORTANTE: Guarde esta senha em local seguro!"
echo "======================================================================"
