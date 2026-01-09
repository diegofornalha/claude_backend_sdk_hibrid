"""
Rotas de Dashboard

Endpoints:
- GET /api/dashboard/stats - Estatisticas gerais
- GET /api/dashboard/recent-activity - Atividade recente
- GET /api/dashboard/mentor - Dashboard do mentor
- GET /api/dashboard/admin - Dashboard do admin

MIGRACAO:
Este modulo esta preparado para migracao gradual do app.py.

COMO MIGRAR:
1. Copiar endpoint do app.py
2. Substituir @app por @router
3. Importar dependencias do core/
4. Testar
5. Remover do app.py
"""

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging

# Importar funcoes de autenticacao
from core.auth import verify_token

# Importar funcoes de roles
from core.roles import get_user_role, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


# ==================================================
# SCHEMAS
# ==================================================

class DashboardStats(BaseModel):
    """Estatisticas do dashboard."""
    total_users: int
    total_sessions: int
    total_messages: int
    active_users_today: int


class RecentActivity(BaseModel):
    """Item de atividade recente."""
    activity_type: str
    description: str
    timestamp: str
    user_id: Optional[int] = None


class MentorDashboard(BaseModel):
    """Dashboard do mentor."""
    total_mentorados: int
    active_mentorados: int
    total_sessions: int
    recent_activity: List[Dict[str, Any]]


class AdminDashboard(BaseModel):
    """Dashboard do admin."""
    total_users: int
    total_mentors: int
    total_mentorados: int
    system_health: Dict[str, Any]


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
# @router.get("/stats", response_model=DashboardStats)
# async def get_dashboard_stats(user_id: int = Depends(get_current_user)):
#     """Obter estatisticas do dashboard"""
#     # ... implementacao ...
#     pass


# ==================================================
# REFERENCIAS - Linhas no app.py para migrar
# ==================================================
#
# GET /api/stats                -> app.py buscar por "stats"
# GET /api/dashboard            -> app.py buscar por "dashboard"
# GET /api/admin/dashboard      -> app.py buscar por "admin/dashboard"
# GET /api/mentor/dashboard     -> app.py buscar por "mentor/dashboard"
# GET /api/recent-activity      -> app.py buscar por "recent-activity"
