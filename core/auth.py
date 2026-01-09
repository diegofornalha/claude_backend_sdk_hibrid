"""
Auth module - Funcoes de autenticacao centralizadas

Contem:
- Hash de senhas (PBKDF2-SHA256)
- Geracao e verificacao de tokens JWT
- Tokens de acesso e refresh

NOTA: As funcoes duplicadas em app.py serao gradualmente migradas para ca.
"""

import os
import jwt
import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# JWT Configuration
JWT_SECRET = os.getenv('JWT_SECRET', 'development_secret_do_not_use_in_production')
JWT_EXPIRATION_HOURS = int(os.getenv('JWT_EXPIRATION_HOURS', '24'))
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv('ACCESS_TOKEN_EXPIRE_HOURS', '6'))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv('REFRESH_TOKEN_EXPIRE_DAYS', '7'))


# =============================================================================
# PASSWORD HASHING
# =============================================================================

def hash_password(password: str, salt: bytes = None) -> str:
    """
    Hash a password with a salt using PBKDF2-SHA256.

    Args:
        password: Plain text password
        salt: Optional salt bytes (generated if not provided)

    Returns:
        Base64 encoded string containing salt + hash
    """
    if not salt:
        salt = os.urandom(32)  # Generate a new salt if not provided

    # Hash the password with the salt
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt,
        100000  # Number of iterations
    )

    # Combine salt and key, then base64 encode for storage in text column
    storage = salt + key
    return base64.b64encode(storage).decode('ascii')


def verify_password(stored_password: str, provided_password: str) -> bool:
    """
    Verify a password against a stored hash.

    Args:
        stored_password: Base64 encoded hash from database
        provided_password: Plain text password to verify

    Returns:
        True if password matches, False otherwise
    """
    try:
        # Decode the base64 stored password
        decoded = base64.b64decode(stored_password.encode('ascii'))

        salt = decoded[:32]  # Get the salt from the stored password
        stored_key = decoded[32:]

        # Hash the provided password with the same salt
        key = hashlib.pbkdf2_hmac(
            'sha256',
            provided_password.encode('utf-8'),
            salt,
            100000  # Same number of iterations as in hash_password
        )

        # Compare the generated key with the stored key
        return key == stored_key
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


# =============================================================================
# JWT TOKEN FUNCTIONS
# =============================================================================

def verify_token(token: str) -> Optional[int]:
    """
    Verify a JWT token and return the user ID if valid.

    Args:
        token: JWT token string

    Returns:
        User ID if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload.get('user_id')
    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        return None
    except jwt.InvalidTokenError:
        logger.warning("Invalid token")
        return None


def create_token(user_id: int) -> str:
    """
    Create a JWT token for a user (legacy, use generate_access_token).

    Args:
        user_id: User ID to encode in token

    Returns:
        JWT token string
    """
    payload = {
        'user_id': user_id,
        'exp': datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS)
    }

    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


def generate_access_token(user_id: int) -> str:
    """
    Generate a JWT access token for the user.

    Access tokens are short-lived (default 6 hours).

    Args:
        user_id: User ID to encode in token

    Returns:
        JWT access token string
    """
    payload = {
        'user_id': user_id,
        'exp': datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
        'type': 'access'
    }

    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


def generate_refresh_token(user_id: int, cursor) -> str:
    """
    Generate a UUID refresh token and save to database.

    Refresh tokens are long-lived (default 7 days).

    Args:
        user_id: User ID
        cursor: Database cursor to save token

    Returns:
        Refresh token string (UUID)
    """
    import uuid

    refresh_token = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    cursor.execute("""
        INSERT INTO refresh_tokens (user_id, refresh_token, expires_at)
        VALUES (%s, %s, %s)
    """, (user_id, refresh_token, expires_at))

    return refresh_token


def verify_refresh_token(refresh_token: str, cursor) -> Optional[int]:
    """
    Verify refresh token and return user_id if valid.

    Args:
        refresh_token: Refresh token string
        cursor: Database cursor

    Returns:
        User ID if valid, None otherwise
    """
    cursor.execute("""
        SELECT user_id, expires_at, revoked
        FROM refresh_tokens
        WHERE refresh_token = %s
    """, (refresh_token,))

    result = cursor.fetchone()
    if not result:
        return None

    user_id, expires_at, revoked = result

    # Check if revoked or expired
    if revoked or datetime.now(timezone.utc) > expires_at:
        return None

    return user_id


def revoke_refresh_token(refresh_token: str, cursor) -> bool:
    """
    Revoke a refresh token.

    Args:
        refresh_token: Refresh token to revoke
        cursor: Database cursor

    Returns:
        True if token was revoked, False otherwise
    """
    cursor.execute("""
        UPDATE refresh_tokens
        SET revoked = 1, revoked_at = %s
        WHERE refresh_token = %s AND revoked = 0
    """, (datetime.now(timezone.utc), refresh_token))

    return cursor.rowcount > 0


def revoke_all_user_tokens(user_id: int, cursor) -> int:
    """
    Revoke all refresh tokens for a user.

    Args:
        user_id: User ID
        cursor: Database cursor

    Returns:
        Number of tokens revoked
    """
    cursor.execute("""
        UPDATE refresh_tokens
        SET revoked = 1, revoked_at = %s
        WHERE user_id = %s AND revoked = 0
    """, (datetime.now(timezone.utc), user_id))

    return cursor.rowcount


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def get_token_expiration() -> dict:
    """Get token expiration settings."""
    return {
        'access_token_hours': ACCESS_TOKEN_EXPIRE_HOURS,
        'refresh_token_days': REFRESH_TOKEN_EXPIRE_DAYS,
        'jwt_hours': JWT_EXPIRATION_HOURS
    }


# =============================================================================
# PERMISSION FUNCTIONS (Hierarchy-based)
# =============================================================================

def get_user_permissions(user_id: int) -> Optional[dict]:
    """
    Obtém permissões do usuário baseado na hierarquia.

    Returns:
        dict com role, admin_level, permissions, can_manage_levels
        None se usuário não encontrado
    """
    from core.admin_level_service import get_admin_level_service

    service = get_admin_level_service()
    return service.get_user_level(user_id)


def check_permission(user_id: int, required_permission: str) -> bool:
    """
    Verifica se usuário tem uma permissão específica.

    Permissões especiais:
    - "*" = todas as permissões (nível 0/Dono)
    - "admin" = qualquer nível admin (admin_level não nulo)

    Args:
        user_id: ID do usuário
        required_permission: Permissão necessária (ex: "view_all", "manage_users")

    Returns:
        True se tem permissão, False caso contrário
    """
    user_info = get_user_permissions(user_id)
    if not user_info:
        return False

    # Se não tem admin_level, não tem permissões admin
    if user_info.get("adminLevel") is None:
        return False

    permissions = user_info.get("permissions", [])

    # Nível 0 (Dono) ou permissão "*" = tudo permitido
    if user_info.get("adminLevel") == 0 or "*" in permissions:
        return True

    # Verificar permissão específica
    return required_permission in permissions


def can_access_admin_area(user_id: int) -> bool:
    """
    Verifica se usuário pode acessar área administrativa.

    Requer:
    - role = "admin" OU
    - admin_level não nulo (qualquer nível da hierarquia)
    """
    user_info = get_user_permissions(user_id)
    if not user_info:
        return False

    # Role admin sempre pode
    if user_info.get("role") == "admin":
        return True

    # Ou se tem admin_level definido
    return user_info.get("adminLevel") is not None


def can_manage_user(manager_id: int, target_user_id: int) -> bool:
    """
    Verifica se manager pode gerenciar target_user.

    Regras:
    - Nível 0 pode gerenciar todos
    - Outros níveis só podem gerenciar níveis que estão em can_manage_levels
    """
    from core.admin_level_service import get_admin_level_service

    service = get_admin_level_service()

    # Obter info dos dois usuários
    manager_info = service.get_user_level(manager_id)
    target_info = service.get_user_level(target_user_id)

    if not manager_info:
        return False

    manager_level = manager_info.get("adminLevel")

    # Sem admin_level não pode gerenciar ninguém
    if manager_level is None:
        return False

    # Nível 0 (Dono) pode gerenciar todos
    if manager_level == 0:
        return True

    # Se target não tem admin_level, é um usuário comum (mentorado)
    # Verificar se pode gerenciar "mentorados" (nível mais alto configurado + 1)
    if not target_info or target_info.get("adminLevel") is None:
        # Assumir que pode gerenciar usuários sem nível admin
        # se tem algum admin_level
        return True

    target_level = target_info.get("adminLevel")
    can_manage = manager_info.get("canManageLevels", [])

    return target_level in can_manage


def get_effective_role(user_id: int) -> str:
    """
    Retorna o role efetivo do usuário considerando hierarquia.

    Returns:
        "admin" se tem admin_level ou role=admin
        "mentorado" caso contrário
    """
    user_info = get_user_permissions(user_id)
    if not user_info:
        return "mentorado"

    if user_info.get("role") == "admin":
        return "admin"

    if user_info.get("adminLevel") is not None:
        return "admin"

    return "mentorado"
