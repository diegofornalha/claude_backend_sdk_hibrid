"""
SDK Routes - Endpoints compatíveis com claude-front-sdk-angular

Endpoints esperados pelo SDK:
- POST /chat/stream - Chat com SSE streaming
- GET /sessions - Listar sessões
- GET /session/current - Sessão atual
- GET /sessions/:id/messages - Mensagens de uma sessão
- POST /reset - Nova sessão
- DELETE /sessions/:id - Deletar sessão
- PATCH /sessions/:id - Atualizar sessão (rename, favorite)
- POST /rag/search - Busca semântica (RAG)
"""

import time
import logging
import json
import asyncio
from typing import Optional, Dict
from fastapi import APIRouter, Request, Response, Header, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.llm_provider import is_using_claude, is_hybrid_mode, needs_tools, get_llm_provider, get_configured_provider
from core.turso_database import get_db_connection
from core.auth import verify_token
from core.session_manager import SessionManager

# White Label: TenantService para prompts dinâmicos
from core.tenant_service import get_tenant_service

# AgentFS para tracking
try:
    from core.agentfs_client import get_agentfs
    AGENTFS_AVAILABLE = True
except ImportError:
    AGENTFS_AVAILABLE = False

# Claude Agent SDK
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
)
from claude_agent_sdk.types import HookMatcher

# Config Manager dinâmico
from core.config_manager import get_config_manager

# Hooks
from core.hooks import (
    validate_sql_query,
    stop_on_critical_error,
    create_track_tool_start,
    create_audit_tool_usage,
)
from tools import platform_mcp_server

logger = logging.getLogger(__name__)

# Router sem prefixo para compatibilidade com SDK
router = APIRouter(tags=["sdk"])

# Session Manager
session_manager = SessionManager(get_db_connection)


# ============================================================================
# Models
# ============================================================================

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    model: Optional[str] = "opus"


class ResetRequest(BaseModel):
    project: Optional[str] = None


class SessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    favorite: Optional[bool] = None
    project_id: Optional[str] = None


class RAGSearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 10
    threshold: Optional[float] = 0.8


# ============================================================================
# Auth Helper - Suporta JWT Bearer e X-API-Key
# ============================================================================

async def get_user_from_auth(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
) -> int:
    """
    Extrai user_id do token JWT ou API Key.
    Prioriza JWT Bearer, fallback para X-API-Key.
    """
    # Tentar JWT Bearer primeiro
    if authorization:
        token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
        user_id = verify_token(token)
        if user_id:
            return user_id

    # Fallback: X-API-Key (para SDK sem auth)
    if x_api_key:
        # Por enquanto, API Key fixa retorna user_id=1 (demo)
        # TODO: Implementar validação de API Key no banco
        return 1

    raise HTTPException(status_code=401, detail="Authorization required")


# Guest Mode - ID fixo para modo convidado
GUEST_USER_ID = 9999


async def get_user_or_guest(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_guest_mode: Optional[str] = Header(None, alias="X-Guest-Mode")
) -> int:
    """
    Retorna user_id do token JWT, API Key ou GUEST_USER_ID para modo convidado.
    """
    # Tentar JWT Bearer primeiro
    if authorization:
        token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
        user_id = verify_token(token)
        if user_id:
            return user_id

    # Fallback: X-API-Key
    if x_api_key:
        return 1  # Demo user

    # Permitir modo convidado por padrão para SDK
    return GUEST_USER_ID


def get_user_role(user_id: int) -> str:
    """Retorna role do usuário"""
    from core.auth import get_effective_role
    return get_effective_role(user_id)


def build_sdk_system_prompt(user_id: int, conversation_id: str) -> str:
    """System prompt simplificado para SDK"""
    tenant_service = get_tenant_service()
    brand = tenant_service.get_brand("default")

    return f"""
Eu sou seu Assistente IA, {brand.brand_tagline}.

INFORMAÇÕES:
- user_id: {user_id}
- session_id: {conversation_id}

MODO: Chat livre para conversas, dúvidas e consultas.

FERRAMENTAS:
- get_session_user_info: Buscar dados do usuário
- update_user_profile: Atualizar perfil

ESTILO:
- Português brasileiro
- Direto e objetivo
- Empático
"""


# ============================================================================
# SSE Streaming Endpoint
# ============================================================================

@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    user_id: int = Depends(get_user_or_guest)
):
    """
    Chat com SSE streaming - compatível com claude-front-sdk-angular

    Formato SSE:
    data: {"text": "chunk"}
    data: {"session_id": "uuid"}
    data: [DONE]
    """
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    session_id = request.session_id

    # Criar sessão se não existir
    if not session_id:
        session_id = await session_manager.create_session(user_id)

    async def generate_sse():
        """Generator para SSE streaming"""
        full_content = ""
        start_time = time.time()

        try:
            # Salvar mensagem do usuário
            await session_manager.save_message(session_id, user_id, "user", message)

            # Enviar session_id
            yield f"data: {json.dumps({'session_id': session_id})}\n\n"

            # Buscar histórico
            history = await session_manager.get_session_history(session_id, limit=50)

            # Formatar histórico
            history_text = ""
            if len(history) > 1:
                history_text = "\n--- HISTÓRICO ---\n"
                for msg in history[:-1]:
                    role_label = "USER" if msg["role"] == "user" else "ASSISTANT"
                    history_text += f"{role_label}: {msg['content']}\n"
                history_text += "--- FIM ---\n\n"

            message_with_context = f"{history_text}USER: {message}" if history_text else message

            # Verificar modo híbrido
            using_claude = is_using_claude()
            hybrid_mode = is_hybrid_mode()
            use_tools = needs_tools(message) if hybrid_mode else False
            should_use_claude = using_claude or (hybrid_mode and use_tools)

            logger.info(f"SDK Chat: user={user_id}, claude={using_claude}, hybrid={hybrid_mode}, tools={use_tools}")

            # Se NÃO usar Claude, usar provider alternativo
            if not should_use_claude:
                try:
                    provider = get_llm_provider()
                    if provider:
                        provider_name, model_name, _ = get_configured_provider()
                        logger.info(f"SDK using provider: {provider_name}")

                        # Preparar mensagens
                        provider_messages = []
                        if len(history) > 1:
                            for msg in history[:-1]:
                                provider_messages.append({
                                    "role": msg["role"],
                                    "content": msg["content"]
                                })
                        provider_messages.append({"role": "user", "content": message})

                        # Stream
                        system_prompt = build_sdk_system_prompt(user_id, session_id)
                        async for chunk in provider.generate_stream(
                            messages=provider_messages,
                            system_prompt=system_prompt
                        ):
                            full_content += chunk
                            yield f"data: {json.dumps({'text': chunk})}\n\n"

                        # Salvar resposta
                        await session_manager.save_message(session_id, user_id, "assistant", full_content)

                        yield "data: [DONE]\n\n"
                        return

                except Exception as provider_error:
                    logger.warning(f"Provider error, falling back to Claude: {provider_error}")
                    should_use_claude = True

            # Usar Claude Agent SDK
            user_role = get_user_role(user_id)
            is_admin = user_role == 'admin'

            # Config Manager
            config_mgr = get_config_manager()

            # Tools baseado no role
            if config_mgr:
                allowed_tools = config_mgr.get_enabled_tools(user_role)
            else:
                if is_admin:
                    allowed_tools = [
                        "mcp__platform__execute_sql_query",
                        "mcp__platform__get_session_user_info",
                    ]
                else:
                    allowed_tools = [
                        "mcp__platform__get_session_user_info",
                        "mcp__platform__update_user_profile",
                    ]

            # Hooks
            user_track_start = create_track_tool_start(user_id)
            user_audit_usage = create_audit_tool_usage(user_id)

            hooks_config = {
                "PreToolUse": [
                    HookMatcher(matcher=None, hooks=[user_track_start]),
                ],
                "PostToolUse": [
                    HookMatcher(matcher=None, hooks=[stop_on_critical_error]),
                    HookMatcher(matcher=None, hooks=[user_audit_usage]),
                ],
            }

            if is_admin:
                hooks_config["PreToolUse"].append(
                    HookMatcher(matcher="mcp__platform__execute_sql_query", hooks=[validate_sql_query])
                )

            # System prompt
            system_prompt = build_sdk_system_prompt(user_id, session_id)

            # Agent options
            options = ClaudeAgentOptions(
                model="claude-sonnet-4-5",
                max_turns=30,
                max_thinking_tokens=8000,
                permission_mode="bypassPermissions",
                system_prompt=system_prompt,
                mcp_servers={"platform": platform_mcp_server},
                allowed_tools=allowed_tools,
                hooks=hooks_config,
            )

            # Stream com Claude
            async with ClaudeSDKClient(options=options) as client:
                await client.query(message_with_context)

                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                full_content += block.text
                                yield f"data: {json.dumps({'text': block.text})}\n\n"

                            elif isinstance(block, ToolUseBlock):
                                yield f"data: {json.dumps({'tool_use': block.name, 'tool_id': block.id})}\n\n"

                            elif isinstance(block, ToolResultBlock):
                                yield f"data: {json.dumps({'tool_result': block.tool_use_id})}\n\n"

                    elif isinstance(msg, ResultMessage):
                        # Salvar resposta
                        await session_manager.save_message(session_id, user_id, "assistant", full_content)

                        # Atualizar custo
                        if msg.total_cost_usd:
                            try:
                                await session_manager.update_session_cost(session_id, msg.total_cost_usd)
                            except:
                                pass

                        break

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"SDK chat error: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ============================================================================
# Session Endpoints
# ============================================================================

@router.get("/sessions")
async def list_sessions(user_id: int = Depends(get_user_or_guest)):
    """Lista sessões do usuário - compatível com SDK (suporta guest mode)"""
    try:
        result = await session_manager.get_user_sessions(user_id, page=1, per_page=50)

        # Formatar para SDK
        sessions = []
        for s in result.get("sessions", []):
            sessions.append({
                "session_id": s.get("session_id"),
                "title": s.get("title") or "Nova conversa",
                "created_at": s.get("created_at"),
                "updated_at": s.get("updated_at"),
                "message_count": s.get("message_count", 0),
                "favorite": s.get("favorite", False),
                "project_id": s.get("project_id"),
            })

        return {"sessions": sessions}

    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/current")
async def get_current_session(user_id: int = Depends(get_user_or_guest)):
    """Retorna sessão atual/mais recente (suporta guest mode)"""
    try:
        result = await session_manager.get_user_sessions(user_id, page=1, per_page=1)
        sessions = result.get("sessions", [])

        if sessions:
            s = sessions[0]
            return {
                "session_id": s.get("session_id"),
                "title": s.get("title"),
                "created_at": s.get("created_at"),
            }

        # Criar nova sessão se não existir
        session_id = await session_manager.create_session(user_id)
        return {"session_id": session_id, "title": "Nova conversa"}

    except Exception as e:
        logger.error(f"Error getting current session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    user_id: int = Depends(get_user_or_guest)
):
    """Retorna mensagens de uma sessão (suporta guest mode)"""
    try:
        messages = await session_manager.get_session_history(session_id, limit=100)

        # Formatar para SDK
        formatted = []
        for msg in messages:
            formatted.append({
                "role": msg.get("role"),
                "content": msg.get("content"),
                "timestamp": msg.get("created_at"),
            })

        return {"messages": formatted}

    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
async def reset_session(
    request: ResetRequest = None,
    user_id: int = Depends(get_user_from_auth)
):
    """Cria nova sessão"""
    try:
        session_id = await session_manager.create_session(user_id)
        return {
            "success": True,
            "session_id": session_id,
            "message": "Nova sessão criada"
        }

    except Exception as e:
        logger.error(f"Error creating session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user_id: int = Depends(get_user_from_auth)
):
    """Deleta uma sessão"""
    try:
        await session_manager.delete_session(session_id, user_id)
        return {"success": True}

    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str,
    request: SessionUpdateRequest,
    user_id: int = Depends(get_user_from_auth)
):
    """Atualiza uma sessão (título, favorito, projeto)"""
    try:
        conn = get_db_connection()
        if not conn:
            raise HTTPException(status_code=500, detail="Database connection failed")

        cursor = conn.cursor()

        updates = []
        values = []

        if request.title is not None:
            updates.append("title = %s")
            values.append(request.title)

        if request.favorite is not None:
            updates.append("favorite = %s")
            values.append(1 if request.favorite else 0)

        if request.project_id is not None:
            updates.append("project_id = %s")
            values.append(request.project_id)

        if updates:
            values.append(session_id)
            values.append(user_id)

            query = f"UPDATE chat_sessions SET {', '.join(updates)} WHERE session_id = %s AND user_id = %s"
            cursor.execute(query, values)
            conn.commit()

        cursor.close()
        conn.close()

        return {"success": True}

    except Exception as e:
        logger.error(f"Error updating session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# RAG Search Endpoint
# ============================================================================

@router.post("/rag/search")
async def rag_search(
    request: RAGSearchRequest,
    user_id: int = Depends(get_user_from_auth)
):
    """Busca semântica RAG"""
    try:
        from core.vector_search import get_vector_search

        vector_search = get_vector_search()
        results = await vector_search.search_similar_messages(
            query=request.query,
            user_id=user_id,
            limit=request.limit,
            threshold=request.threshold
        )

        return {
            "success": True,
            "query": request.query,
            "results": results,
            "count": len(results)
        }

    except Exception as e:
        logger.error(f"RAG search error: {e}")
        return {"success": False, "error": str(e), "results": []}


# ============================================================================
# LLM Config Endpoints
# ============================================================================

# Modelos disponíveis por provider
AVAILABLE_MODELS = {
    "claude": ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku"],
    "minimax": ["MiniMax-M2"],
    "hybrid": ["MiniMax-M2 + Claude (tools)"],
    "openrouter": [
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "google/gemini-2.0-flash-exp",
        "meta-llama/llama-3.3-70b-instruct",
        "anthropic/claude-sonnet-4"
    ]
}


class LLMConfigUpdate(BaseModel):
    provider: str
    model: str
    api_key: Optional[str] = None


@router.get("/config/llm")
async def get_llm_config(user_id: int = Depends(get_user_from_auth)):
    """Retorna configuração LLM atual"""
    try:
        from core.config_manager import get_config_manager

        config_mgr = get_config_manager()
        if not config_mgr:
            return {
                "success": True,
                "provider": "claude",
                "model": "claude-opus-4-5",
                "has_api_key": False,
                "available_providers": ["claude", "hybrid", "minimax", "openrouter"],
                "available_models": AVAILABLE_MODELS
            }

        return {
            "success": True,
            "provider": config_mgr.get_config("llm_provider", "claude"),
            "model": config_mgr.get_config("llm_model", "claude-opus-4-5"),
            "has_api_key": bool(config_mgr.get_config("llm_api_key")),
            "available_providers": ["claude", "hybrid", "minimax", "openrouter"],
            "available_models": AVAILABLE_MODELS
        }

    except Exception as e:
        logger.error(f"Error getting LLM config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config/llm")
async def update_llm_config(
    request: LLMConfigUpdate,
    user_id: int = Depends(get_user_from_auth)
):
    """Atualiza configuração LLM"""
    try:
        from core.config_manager import get_config_manager

        valid_providers = ["claude", "hybrid", "minimax", "openrouter"]
        if request.provider not in valid_providers:
            raise HTTPException(
                status_code=400,
                detail=f"Provider inválido: '{request.provider}'"
            )

        if request.model not in AVAILABLE_MODELS.get(request.provider, []):
            raise HTTPException(
                status_code=400,
                detail=f"Modelo '{request.model}' não disponível para '{request.provider}'"
            )

        config_mgr = get_config_manager()
        if not config_mgr:
            raise HTTPException(status_code=500, detail="Config manager not initialized")

        config_mgr.set_config("llm_provider", request.provider, user_id)
        config_mgr.set_config("llm_model", request.model, user_id)

        if request.api_key:
            config_mgr.set_config("llm_api_key", request.api_key, user_id)

        logger.info(f"LLM config updated: {request.provider}/{request.model} by user {user_id}")

        return {
            "success": True,
            "message": f"Configuração atualizada: {request.provider} / {request.model}",
            "provider": request.provider,
            "model": request.model
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating LLM config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Guest Mode - Chat endpoint separado (deprecated - usar /chat/stream)
# ============================================================================

@router.post("/chat/stream/guest")
async def chat_stream_guest(request: ChatRequest):
    """
    Chat com SSE streaming para modo convidado (sem autenticação).
    Histórico não é persistido.
    """
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    async def generate_sse():
        """Generator para SSE streaming no modo convidado"""
        full_content = ""

        try:
            # Gerar session_id temporário
            import uuid
            session_id = f"guest-{uuid.uuid4()}"
            yield f"data: {json.dumps({'session_id': session_id})}\n\n"

            # Verificar modo híbrido
            using_claude = is_using_claude()
            hybrid_mode = is_hybrid_mode()
            use_tools = needs_tools(message) if hybrid_mode else False
            should_use_claude = using_claude or (hybrid_mode and use_tools)

            logger.info(f"Guest Chat: claude={using_claude}, hybrid={hybrid_mode}, tools={use_tools}")

            # Se NÃO usar Claude, usar provider alternativo
            if not should_use_claude:
                try:
                    provider = get_llm_provider()
                    if provider:
                        provider_name, model_name, _ = get_configured_provider()

                        messages = [{"role": "user", "content": message}]
                        system_prompt = "Você é um assistente IA prestativo. Responda em português brasileiro."

                        async for chunk in provider.generate_stream(
                            messages=messages,
                            system_prompt=system_prompt
                        ):
                            full_content += chunk
                            yield f"data: {json.dumps({'text': chunk})}\n\n"

                        yield "data: [DONE]\n\n"
                        return

                except Exception as provider_error:
                    logger.warning(f"Provider error in guest mode: {provider_error}")
                    should_use_claude = True

            # Usar Claude Agent SDK
            system_prompt = "Você é um assistente IA prestativo. Responda em português brasileiro de forma clara e objetiva."

            options = ClaudeAgentOptions(
                model="claude-sonnet-4-5",
                max_turns=10,
                max_thinking_tokens=4000,
                permission_mode="bypassPermissions",
                system_prompt=system_prompt,
                allowed_tools=[],  # Sem ferramentas no modo convidado
            )

            async with ClaudeSDKClient(options=options) as client:
                await client.query(message)

                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                full_content += block.text
                                yield f"data: {json.dumps({'text': block.text})}\n\n"

                    elif isinstance(msg, ResultMessage):
                        break

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"Guest chat error: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
