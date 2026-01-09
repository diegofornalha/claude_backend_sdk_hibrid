"""
Rotas de Autenticacao

Endpoints:
- POST /api/auth/register - Registrar novo usuario
- POST /api/auth/login - Login
- POST /api/auth/logout - Logout
- POST /api/auth/refresh - Refresh token
- POST /api/auth/send-otp - Enviar OTP
- POST /api/auth/verify-otp - Verificar OTP
- POST /api/auth/change-password - Alterar senha
- GET /api/auth/check-existing - Verificar se usuario existe

MIGRACAO:
Este modulo esta preparado para migracao gradual do app.py.
As funcoes de autenticacao estao em core/auth.py.

COMO MIGRAR:
1. Copiar endpoint do app.py
2. Substituir @app por @router
3. Importar dependencias do core/
4. Testar
5. Remover do app.py

Dependencias disponiveis em core/auth.py:
- hash_password(password, salt=None)
- verify_password(stored_password, provided_password)
- verify_token(token)
- create_token(user_id)
- generate_access_token(user_id)
- generate_refresh_token(user_id, cursor)
- verify_refresh_token(refresh_token, cursor)
- revoke_refresh_token(refresh_token, cursor)
- revoke_all_user_tokens(user_id, cursor)
"""

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any
import logging

# Importar funcoes de autenticacao centralizadas
from core.auth import (
    hash_password,
    verify_password,
    verify_token,
    create_token,
    generate_access_token,
    generate_refresh_token,
    verify_refresh_token,
    revoke_refresh_token,
    revoke_all_user_tokens,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# ==================================================
# SCHEMAS
# ==================================================

class RegisterRequest(BaseModel):
    """Request para registro de usuario."""
    username: str
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    password: str
    role: Optional[str] = "mentorado"
    mentor_id: Optional[int] = None


class LoginRequest(BaseModel):
    """Request para login."""
    identifier: str  # Email ou telefone
    password: str


class TokenResponse(BaseModel):
    """Response com tokens."""
    token: str
    refresh_token: Optional[str] = None
    user: Dict[str, Any]


class RefreshRequest(BaseModel):
    """Request para refresh token."""
    refresh_token: str


class LogoutRequest(BaseModel):
    """Request para logout."""
    refresh_token: str


class OTPRequest(BaseModel):
    """Request para enviar OTP."""
    phone_number: str


class OTPVerifyRequest(BaseModel):
    """Request para verificar OTP."""
    phone_number: str
    otp: str


class ChangePasswordRequest(BaseModel):
    """Request para alterar senha."""
    old_password: str
    new_password: str


# ==================================================
# DEPENDENCY FUNCTIONS
# ==================================================

async def get_current_user(request: Request) -> int:
    """
    Extrai user_id do token JWT no header Authorization.

    Uso: user_id: int = Depends(get_current_user)
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticacao nao fornecido"
        )

    token = auth_header.replace("Bearer ", "")
    user_id = verify_token(token)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido ou expirado"
        )

    return user_id


# ==================================================
# ENDPOINTS
# Os endpoints abaixo estao prontos para serem migrados do app.py.
# Basta descomentar e ajustar conforme necessario.
# ==================================================

# Exemplo de endpoint migrado (descomentado quando pronto):
#
# @router.get("/check-existing")
# async def check_existing_user(email: str = None, username: str = None):
#     """Check if username or email already exists"""
#     # ... implementacao ...
#     pass


# ==================================================
# REFERENCIAS - Linhas no app.py para migrar
# ==================================================
#
# /api/auth/check-existing     -> app.py:1493
# /api/auth/register           -> app.py:1545
# /api/auth/force-cleanup      -> app.py:1631
# /api/auth/verify-registration-> app.py:1654
# /api/auth/login              -> app.py:1782
# /api/auth/refresh            -> app.py:1849
# /api/auth/logout             -> app.py:1905
# /api/auth/send-otp           -> app.py:1936
# /api/auth/verify-otp         -> app.py:2048
# /api/auth/resend-otp         -> app.py:2157
# /api/auth/change-password    -> app.py:2262
# /api/auth/mentors            -> app.py:5238 (mover para user_routes)
