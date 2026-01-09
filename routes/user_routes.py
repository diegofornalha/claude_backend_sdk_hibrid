"""
Rotas de Usuarios

Endpoints:
- GET /api/users/{user_id} - Obter usuario
- GET /api/users/me - Obter usuario atual
- PATCH /api/users/{user_id} - Atualizar usuario
- DELETE /api/users/{user_id} - Deletar usuario
- DELETE /api/user/delete-account - Deletar propria conta
- GET /api/users/mentors - Listar mentores
- GET /api/users/mentorados - Listar mentorados (mentor)

MIGRACAO:
Este modulo esta preparado para migracao gradual do app.py.
As funcoes de autenticacao estao em core/auth.py.
As funcoes de roles estao em core/roles.py.

COMO MIGRAR:
1. Copiar endpoint do app.py
2. Substituir @app por @router
3. Importar dependencias do core/
4. Testar
5. Remover do app.py
"""

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
import logging

# Importar funcoes de autenticacao
from core.auth import verify_token

# Importar funcoes de roles
from core.roles import get_user_role, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["Users"])


# ==================================================
# SCHEMAS
# ==================================================

class UserUpdateRequest(BaseModel):
    """Request para atualizar usuario."""
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    profession: Optional[str] = None
    specialty: Optional[str] = None
    years_experience: Optional[int] = None
    current_revenue: Optional[float] = None
    desired_revenue: Optional[float] = None
    main_challenge: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None


class UserResponse(BaseModel):
    """Response com dados do usuario."""
    user_id: int
    username: str
    email: Optional[str] = None
    phone_number: Optional[str] = None
    role: str
    profession: Optional[str] = None
    specialty: Optional[str] = None
    years_experience: Optional[int] = None
    current_revenue: Optional[float] = None
    desired_revenue: Optional[float] = None
    city: Optional[str] = None
    state: Optional[str] = None
    registration_date: Optional[str] = None
    account_status: Optional[str] = None


class MentorResponse(BaseModel):
    """Response com dados do mentor."""
    user_id: int
    username: str
    email: Optional[str] = None


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
# ==================================================

# Exemplo de endpoint migrado (descomentado quando pronto):
#
# @router.get("/me", response_model=UserResponse)
# async def get_current_user_profile(user_id: int = Depends(get_current_user)):
#     """Obter perfil do usuario atual"""
#     # ... implementacao ...
#     pass


# ==================================================
# EVOLUTION / PROMOTION ENDPOINTS
# ==================================================

from core.evolution_service import get_evolution_service, EvolutionService
import sqlite3
import os


class PromoteUserRequest(BaseModel):
    """Request para promover usuário."""
    toStage: str


class PromotionResponse(BaseModel):
    """Resposta de promoção."""
    success: bool
    userId: int
    fromStage: str
    toStage: str
    newTenantId: Optional[str] = None
    message: str


class UserStageResponse(BaseModel):
    """Informações do estágio do usuário."""
    userId: int
    username: str
    email: Optional[str]
    role: str
    tenantId: str
    currentStageKey: str
    stageName: str
    stageLevel: int
    stageType: str
    stageHistory: List[Dict[str, Any]] = []
    promotedToTenantId: Optional[str] = None


def get_evolution() -> EvolutionService:
    """Obtém o EvolutionService."""
    return get_evolution_service()


async def get_current_user_with_role(request: Request) -> Dict[str, Any]:
    """Extrai user_id e role do token JWT."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação não fornecido"
        )

    token = auth_header.replace("Bearer ", "")
    user_id = verify_token(token)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado"
        )

    # Buscar role do usuário
    db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'crm.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        cursor = conn.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        role = row["role"] if row else "mentorado"
    finally:
        conn.close()

    return {"user_id": user_id, "role": role}


@router.get("/{user_id}/stage", response_model=UserStageResponse)
async def get_user_stage(
    user_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user_with_role),
    evolution: EvolutionService = Depends(get_evolution)
):
    """
    Obtém informações do estágio do usuário.

    Admin pode ver qualquer usuário.
    Usuário comum só pode ver seu próprio estágio.
    """
    if current_user.get("role") != "admin" and current_user.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    stage_info = evolution.get_user_stage(user_id)
    if not stage_info:
        raise HTTPException(status_code=404, detail="User not found")

    return stage_info


@router.post("/{user_id}/promote", response_model=PromotionResponse)
async def promote_user(
    user_id: int,
    request: PromoteUserRequest,
    current_user: Dict[str, Any] = Depends(get_current_user_with_role),
    evolution: EvolutionService = Depends(get_evolution)
):
    """
    Promove usuário para um novo estágio (admin only).

    Se o estágio destino tem creates_tenant=True, cria novo tenant
    e o usuário se torna admin/owner do novo tenant.
    """
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = evolution.promote_user(
        user_id=user_id,
        to_stage_key=request.toStage,
        promoted_by=current_user.get("user_id"),
        tenant_id="default",
    )

    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)

    logger.info(f"User {user_id} promoted to {request.toStage} by admin {current_user.get('user_id')}")

    return result.to_dict()


@router.get("/by-stage/{stage_key}")
async def get_users_by_stage(
    stage_key: str,
    tenant_id: str = "default",
    limit: int = 100,
    offset: int = 0,
    current_user: Dict[str, Any] = Depends(get_current_user_with_role),
    evolution: EvolutionService = Depends(get_evolution)
):
    """
    Lista usuários em um determinado estágio (admin only).
    """
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    users = evolution.get_users_by_stage(stage_key, tenant_id, limit, offset)
    return {"users": users, "count": len(users)}


# ==================================================
# REFERENCIAS - Linhas no app.py para migrar
# ==================================================
#
# GET /api/user                -> app.py:2406 (obter usuario atual)
# GET /api/users/{user_id}     -> app.py:2406 (obter usuario por ID)
# PATCH /api/users/{user_id}   -> app.py:2314 (atualizar usuario)
# DELETE /api/user/delete-account -> app.py:2450 (deletar propria conta)
# GET /api/auth/mentors        -> app.py:5238 (listar mentores)
# GET /api/mentor/mentorados   -> app.py:4472 (listar mentorados)
# POST /api/mentor/vincular-mentorado -> app.py:4519 (vincular mentorado)
