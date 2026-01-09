"""
Admin Config Routes - Endpoints para gerenciar configurações de agentes e ferramentas

Permite ao admin:
- Listar/ativar/desativar agentes
- Listar/ativar/desativar ferramentas MCP
- Alterar modelo dos agentes
- Definir permissões por role
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from typing import List, Optional

from core.turso_database import get_db_connection
from core.auth import verify_token
from core.config_manager import get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/config", tags=["admin-config"])


# =============================================================================
# Modelos de Request/Response
# =============================================================================

class AgentStatusUpdate(BaseModel):
    """Request para atualizar status de um agente"""
    enabled: bool


class AgentModelUpdate(BaseModel):
    """Request para atualizar modelo de um agente"""
    model: str  # opus, sonnet, haiku


class AgentRolesUpdate(BaseModel):
    """Request para atualizar roles de um agente"""
    roles: List[str]  # ["admin", "mentorado"]


class ToolStatusUpdate(BaseModel):
    """Request para atualizar status de uma ferramenta"""
    enabled: bool


class BulkAgentUpdate(BaseModel):
    """Request para atualizar múltiplos agentes de uma vez"""
    agents: dict  # {"agent-name": true/false, ...}


class BulkToolUpdate(BaseModel):
    """Request para atualizar múltiplas ferramentas de uma vez"""
    tools: dict  # {"tool-name": true/false, ...}


class LLMConfigUpdate(BaseModel):
    """Request para atualizar configuração LLM"""
    provider: str  # claude, minimax, openrouter
    model: str
    api_key: Optional[str] = None


# Modelos disponíveis por provider
AVAILABLE_MODELS = {
    "claude": ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku"],
    "minimax": ["MiniMax-M2"],
    "hybrid": ["MiniMax-M2 + Claude (tools)"],  # MiniMax para chat, Claude para ferramentas
    "openrouter": [
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "google/gemini-2.0-flash-exp",
        "meta-llama/llama-3.3-70b-instruct",
        "anthropic/claude-sonnet-4"
    ]
}


# =============================================================================
# Helpers
# =============================================================================

def get_admin_user(authorization: Optional[str] = Header(None)) -> int:
    """Verifica se é admin e retorna user_id"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    user_id = verify_token(token)

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Verificar se é admin usando effective role (considera admin_level)
    from core.auth import get_effective_role
    effective_role = get_effective_role(user_id)

    if effective_role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    return user_id


# =============================================================================
# Endpoints de Agentes
# =============================================================================

@router.get("/agents")
async def list_agents(user_id: int = Depends(get_admin_user)):
    """
    Lista todos os agentes e seu status.

    Retorna para cada agente:
    - name: Nome do agente
    - description: Descrição
    - enabled: Se está ativo
    - model: Modelo usado (opus/sonnet/haiku)
    - allowed_roles: Roles que podem usar
    """

    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    agents = config_mgr.get_all_agents_status()

    return {
        "success": True,
        "agents": agents,
        "total": len(agents)
    }


@router.put("/agents/{agent_name}/status")
async def update_agent_status(
    agent_name: str,
    update: AgentStatusUpdate,
    user_id: int = Depends(get_admin_user)
):
    """
    Ativa ou desativa um agente específico.

    Args:
        agent_name: Nome do agente (ex: diagnostic-expert)
        update.enabled: True para ativar, False para desativar
    """

    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    success = config_mgr.update_agent_status(agent_name, update.enabled, user_id)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update agent status")

    action = "ativado" if update.enabled else "desativado"
    logger.info(f"Agent '{agent_name}' {action} by admin {user_id}")

    return {
        "success": True,
        "message": f"Agente '{agent_name}' {action} com sucesso",
        "agent": agent_name,
        "enabled": update.enabled
    }


@router.put("/agents/{agent_name}/model")
async def update_agent_model(
    agent_name: str,
    update: AgentModelUpdate,
    user_id: int = Depends(get_admin_user)
):
    """
    Altera o modelo usado por um agente.

    Args:
        agent_name: Nome do agente
        update.model: 'opus', 'sonnet', ou 'haiku'
    """

    if update.model not in ["opus", "sonnet", "haiku"]:
        raise HTTPException(
            status_code=400,
            detail="Modelo deve ser 'opus', 'sonnet' ou 'haiku'"
        )

    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    success = config_mgr.update_agent_model(agent_name, update.model, user_id)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update agent model")

    logger.info(f"Agent '{agent_name}' model changed to '{update.model}' by admin {user_id}")

    return {
        "success": True,
        "message": f"Modelo do agente '{agent_name}' alterado para '{update.model}'",
        "agent": agent_name,
        "model": update.model
    }


@router.put("/agents/{agent_name}/roles")
async def update_agent_roles(
    agent_name: str,
    update: AgentRolesUpdate,
    user_id: int = Depends(get_admin_user)
):
    """
    Define quais roles podem usar um agente.

    Args:
        agent_name: Nome do agente
        update.roles: Lista de roles ['admin', 'mentorado']
    """

    valid_roles = ["admin", "mentorado"]
    for role in update.roles:
        if role not in valid_roles:
            raise HTTPException(
                status_code=400,
                detail=f"Role inválido: '{role}'. Use: {valid_roles}"
            )

    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    success = config_mgr.update_agent_roles(agent_name, update.roles, user_id)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update agent roles")

    logger.info(f"Agent '{agent_name}' roles updated to {update.roles} by admin {user_id}")

    return {
        "success": True,
        "message": f"Roles do agente '{agent_name}' atualizados",
        "agent": agent_name,
        "roles": update.roles
    }


@router.put("/agents/bulk")
async def bulk_update_agents(
    update: BulkAgentUpdate,
    user_id: int = Depends(get_admin_user)
):
    """
    Atualiza status de múltiplos agentes de uma vez.

    Body:
        {"agents": {"diagnostic-expert": true, "sql-analyst": false, ...}}
    """

    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    success = config_mgr.set_config("enabled_agents", update.agents, user_id)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to bulk update agents")

    enabled_count = sum(1 for v in update.agents.values() if v)
    logger.info(f"Bulk agent update: {enabled_count}/{len(update.agents)} enabled by admin {user_id}")

    return {
        "success": True,
        "message": f"{enabled_count} agentes ativados, {len(update.agents) - enabled_count} desativados",
        "agents": update.agents
    }


# =============================================================================
# Endpoints de Ferramentas
# =============================================================================

@router.get("/tools")
async def list_tools(user_id: int = Depends(get_admin_user)):
    """
    Lista todas as ferramentas MCP e seu status.

    Retorna para cada ferramenta:
    - name: Nome curto
    - full_name: Nome completo (mcp__platform__ ou mcp__crm__)
    - description: Descrição
    - enabled: Se está ativa
    """

    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    tools = config_mgr.get_all_tools_status()

    return {
        "success": True,
        "tools": tools,
        "total": len(tools)
    }


@router.put("/tools/{tool_name}/status")
async def update_tool_status(
    tool_name: str,
    update: ToolStatusUpdate,
    user_id: int = Depends(get_admin_user)
):
    """
    Ativa ou desativa uma ferramenta MCP.

    Args:
        tool_name: Nome da ferramenta (sem prefixo mcp__platform__ ou mcp__crm__)
        update.enabled: True para ativar, False para desativar
    """

    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    success = config_mgr.update_tool_status(tool_name, update.enabled, user_id)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update tool status")

    action = "ativada" if update.enabled else "desativada"
    logger.info(f"Tool '{tool_name}' {action} by admin {user_id}")

    return {
        "success": True,
        "message": f"Ferramenta '{tool_name}' {action} com sucesso",
        "tool": tool_name,
        "enabled": update.enabled
    }


@router.put("/tools/bulk")
async def bulk_update_tools(
    update: BulkToolUpdate,
    user_id: int = Depends(get_admin_user)
):
    """
    Atualiza status de múltiplas ferramentas de uma vez.

    Body:
        {"tools": {"execute_sql_query": true, "save_diagnosis": false, ...}}
    """

    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    success = config_mgr.set_config("enabled_tools", update.tools, user_id)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to bulk update tools")

    enabled_count = sum(1 for v in update.tools.values() if v)
    logger.info(f"Bulk tool update: {enabled_count}/{len(update.tools)} enabled by admin {user_id}")

    return {
        "success": True,
        "message": f"{enabled_count} ferramentas ativadas, {len(update.tools) - enabled_count} desativadas",
        "tools": update.tools
    }


# =============================================================================
# Endpoint de Configuração Geral
# =============================================================================

@router.get("/summary")
async def get_config_summary(user_id: int = Depends(get_admin_user)):
    """
    Retorna resumo completo da configuração atual.

    Inclui agentes, ferramentas e estatísticas.
    """

    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    agents = config_mgr.get_all_agents_status()
    tools = config_mgr.get_all_tools_status()

    enabled_agents = sum(1 for a in agents if a["enabled"])
    enabled_tools = sum(1 for t in tools if t["enabled"])

    return {
        "success": True,
        "summary": {
            "agents": {
                "total": len(agents),
                "enabled": enabled_agents,
                "disabled": len(agents) - enabled_agents
            },
            "tools": {
                "total": len(tools),
                "enabled": enabled_tools,
                "disabled": len(tools) - enabled_tools
            }
        },
        "agents": agents,
        "tools": tools
    }


# =============================================================================
# Endpoints de LLM Provider
# =============================================================================

@router.get("/llm")
async def get_llm_config(user_id: int = Depends(get_admin_user)):
    """
    Retorna configuração atual de LLM provider.

    Retorna:
    - provider: Provider ativo (claude, minimax, openrouter)
    - model: Modelo atual
    - has_api_key: Se tem API key configurada
    - available_providers: Lista de providers disponíveis
    - available_models: Modelos por provider
    """

    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    return {
        "success": True,
        "provider": config_mgr.get_config("llm_provider", "claude"),
        "model": config_mgr.get_config("llm_model", "claude-opus-4-5"),
        "has_api_key": bool(config_mgr.get_config("llm_api_key")),
        "available_providers": ["claude", "hybrid", "minimax", "openrouter"],
        "available_models": AVAILABLE_MODELS
    }


@router.put("/llm")
async def update_llm_config(
    update: LLMConfigUpdate,
    user_id: int = Depends(get_admin_user)
):
    """
    Atualiza configuração de LLM provider (apenas admin).

    Args:
        update.provider: 'claude', 'minimax' ou 'openrouter'
        update.model: Nome do modelo
        update.api_key: API key (opcional, para minimax/openrouter)
    """

    valid_providers = ["claude", "hybrid", "minimax", "openrouter"]
    if update.provider not in valid_providers:
        raise HTTPException(
            status_code=400,
            detail=f"Provider inválido: '{update.provider}'. Use: {valid_providers}"
        )

    # Validar modelo para o provider
    if update.model not in AVAILABLE_MODELS.get(update.provider, []):
        raise HTTPException(
            status_code=400,
            detail=f"Modelo '{update.model}' não disponível para provider '{update.provider}'"
        )

    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    # Atualizar configurações
    config_mgr.set_config("llm_provider", update.provider, user_id)
    config_mgr.set_config("llm_model", update.model, user_id)

    if update.api_key:
        config_mgr.set_config("llm_api_key", update.api_key, user_id)

    logger.info(f"LLM config updated: provider={update.provider}, model={update.model} by admin {user_id}")

    return {
        "success": True,
        "message": f"Configuração LLM atualizada: {update.provider} / {update.model}",
        "provider": update.provider,
        "model": update.model
    }


# =============================================================================
# Endpoints de LLM para TODOS os usuários autenticados
# =============================================================================

def get_authenticated_user(authorization: Optional[str] = Header(None)) -> int:
    """Verifica se está autenticado e retorna user_id (qualquer role)"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    user_id = verify_token(token)

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return user_id


# Router separado para endpoints de usuário (sem /admin)
user_config_router = APIRouter(prefix="/api/config", tags=["user-config"])


@user_config_router.get("/llm")
async def get_user_llm_config(user_id: int = Depends(get_authenticated_user)):
    """
    Retorna configuração atual de LLM provider (acesso para qualquer usuário autenticado).
    """
    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    return {
        "success": True,
        "provider": config_mgr.get_config("llm_provider", "claude"),
        "model": config_mgr.get_config("llm_model", "claude-opus-4-5"),
        "has_api_key": bool(config_mgr.get_config("llm_api_key")),
        "available_providers": ["claude", "hybrid", "minimax", "openrouter"],
        "available_models": AVAILABLE_MODELS
    }


@user_config_router.put("/llm")
async def update_user_llm_config(
    update: LLMConfigUpdate,
    user_id: int = Depends(get_authenticated_user)
):
    """
    Atualiza configuração de LLM provider (acesso para qualquer usuário autenticado).
    """
    valid_providers = ["claude", "hybrid", "minimax", "openrouter"]
    if update.provider not in valid_providers:
        raise HTTPException(
            status_code=400,
            detail=f"Provider inválido: '{update.provider}'. Use: {valid_providers}"
        )

    if update.model not in AVAILABLE_MODELS.get(update.provider, []):
        raise HTTPException(
            status_code=400,
            detail=f"Modelo '{update.model}' não disponível para provider '{update.provider}'"
        )

    config_mgr = get_config_manager()
    if not config_mgr:
        raise HTTPException(status_code=500, detail="Config manager not initialized")

    config_mgr.set_config("llm_provider", update.provider, user_id)
    config_mgr.set_config("llm_model", update.model, user_id)

    if update.api_key:
        config_mgr.set_config("llm_api_key", update.api_key, user_id)

    logger.info(f"LLM config updated: provider={update.provider}, model={update.model} by user {user_id}")

    return {
        "success": True,
        "message": f"Configuração LLM atualizada: {update.provider} / {update.model}",
        "provider": update.provider,
        "model": update.model
    }
