"""
Chat Tools - Ferramentas para gerenciar histórico de chat

Permite que a Nanda busque sessões, mensagens e dados de usuários.
"""

import json
import logging
from typing import Dict, Any

from claude_agent_sdk import tool

logger = logging.getLogger(__name__)


@tool(
    "get_user_chat_sessions",
    """Busca o histórico de conversas (chat sessions) de um usuário específico.

    USE ESTA FERRAMENTA quando o admin perguntar sobre:
    - "histórico de fontes" de um mentorado
    - "conversas" de um mentorado
    - "chat sessions" de um usuário

    Parâmetros:
    - user_id: ID do usuário (número inteiro)
    - limit: Quantidade máxima de sessões (padrão: 10)

    Retorna:
    - Lista de sessões com: session_id, title, created_at, message_count
    - Total de sessões encontradas
    """,
    {
        "user_id": int,
        "limit": int
    }
)
async def get_user_chat_sessions(args: Dict[str, Any]) -> Dict:
    """
    Busca as sessões de chat de um usuário.

    Args:
        user_id: ID do usuário
        limit: Quantidade máxima (default: 10)

    Returns:
        Lista de chat_sessions com detalhes
    """
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import get_db_connection

    try:
        user_id = args.get("user_id")
        limit = args.get("limit", 10)

        if not user_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "Erro: user_id é obrigatório"
                }],
                "isError": True
            }

        conn = get_db_connection()
        if not conn:
            return {
                "content": [{
                    "type": "text",
                    "text": "Erro: Falha na conexão com o banco de dados"
                }],
                "isError": True
            }

        # Usar API do Turso/SQLite
        sessions = conn.query("""
            SELECT
                cs.session_id,
                cs.title,
                cs.created_at,
                cs.updated_at,
                cs.total_cost_usd,
                (SELECT COUNT(*) FROM chat_messages WHERE session_id = cs.session_id) as message_count
            FROM chat_sessions cs
            WHERE cs.user_id = ?
            ORDER BY cs.updated_at DESC
            LIMIT ?
        """, (user_id, limit))

        # Montar resposta
        result = {
            "found": len(sessions) > 0,
            "total": len(sessions),
            "user_id": user_id,
            "sessions": [
                {
                    "session_id": s["session_id"],
                    "title": s["title"],
                    "created_at": str(s["created_at"]) if s.get("created_at") else None,
                    "updated_at": str(s["updated_at"]) if s.get("updated_at") else None,
                    "message_count": s["message_count"],
                    "total_cost_usd": float(s["total_cost_usd"]) if s.get("total_cost_usd") else 0,
                    "url": f"https://mvp.nandamac.cloud/admin/chat/{s['session_id']}"
                }
                for s in sessions
            ]
        }

        if not sessions:
            result["message"] = f"Nenhuma conversa encontrada para user_id={user_id}"

        logger.info(f"Found {len(sessions)} chat sessions for user {user_id}")

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, default=str, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"Error getting user chat sessions: {e}")
        return {
            "content": [{
                "type": "text",
                "text": f"Erro ao buscar sessões de chat: {str(e)}"
            }],
            "isError": True
        }


@tool(
    "get_session_user_info",
    """Busca informações do usuário associado a uma sessão de chat específica.

    USE ESTA FERRAMENTA quando precisar saber:
    - Nome do usuário em uma conversa
    - Email do usuário de uma sessão
    - Profissão/dados de quem está conversando

    Parâmetros:
    - session_id: ID da sessão de chat (string)

    Retorna:
    - Dados completos do usuário: nome, email, profissão, telefone, etc.
    """,
    {
        "session_id": str
    }
)
async def get_session_user_info(args: Dict[str, Any]) -> Dict:
    """
    Busca dados do usuário associado a uma sessão de chat.

    Args:
        session_id: ID da sessão

    Returns:
        Informações do usuário
    """
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import get_db_connection

    try:
        session_id = args.get("session_id")

        if not session_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "Erro: session_id é obrigatório"
                }],
                "isError": True
            }

        conn = get_db_connection()
        if not conn:
            return {
                "content": [{
                    "type": "text",
                    "text": "Erro: Falha na conexão com o banco de dados"
                }],
                "isError": True
            }

        # Log para debug
        logger.info(f"[get_session_user_info] Buscando usuário para session_id={session_id}")

        # Buscar usuário da sessão usando API do Turso
        users = conn.query("""
            SELECT
                u.user_id,
                u.username,
                u.email,
                u.role,
                u.profession,
                u.phone_number,
                u.registration_date as user_created_at,
                cs.session_id,
                cs.title as session_title,
                cs.created_at as session_created_at
            FROM chat_sessions cs
            JOIN users u ON cs.user_id = u.user_id
            WHERE cs.session_id = ?
            LIMIT 1
        """, (session_id,))

        user = users[0] if users else None

        logger.info(f"[get_session_user_info] Query result: {user}")

        if not user:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "found": False,
                        "session_id": session_id,
                        "message": f"Sessão '{session_id}' não encontrada ou não possui usuário associado"
                    }, ensure_ascii=False, indent=2)
                }]
            }

        # Montar resposta
        result = {
            "found": True,
            "session_id": session_id,
            "user": {
                "user_id": user["user_id"],
                "username": user["username"],
                "email": user["email"],
                "role": user["role"],
                "profession": user["profession"] or "Não informado",
                "phone": user["phone_number"] or "Não informado",
                "user_created_at": str(user["user_created_at"]) if user.get("user_created_at") else None,
            },
            "session": {
                "title": user["session_title"],
                "created_at": str(user["session_created_at"]) if user.get("session_created_at") else None,
            }
        }

        logger.info(f"Found user {user['user_id']} for session {session_id}")

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, default=str, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[get_session_user_info] Error: {e}", exc_info=True)
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "found": False,
                    "session_id": session_id,
                    "error": str(e),
                    "message": "Erro ao buscar informações do usuário. Verifique se a sessão existe e possui usuário associado."
                }, ensure_ascii=False, indent=2)
            }],
            "isError": True
        }


@tool(
    "update_user_profile",
    """Atualiza informações do perfil de um usuário.

    USE ESTA FERRAMENTA quando o usuário pedir para:
    - Atualizar/mudar/alterar seu nome
    - Atualizar/mudar/alterar seu email
    - Atualizar/mudar/alterar sua profissão
    - Atualizar/mudar/alterar sua especialidade
    - Atualizar/mudar/alterar seu telefone

    IMPORTANTE: Esta ferramenta SÓ funciona para o usuário da sessão atual.
    O usuário só pode atualizar seus PRÓPRIOS dados.

    Parâmetros:
    - session_id: ID da sessão de chat atual (para identificar o usuário)
    - field: Campo a atualizar (username, email, profession, specialty, phone)
    - value: Novo valor para o campo

    Retorna:
    - Confirmação da atualização com dados atuais do usuário
    """,
    {
        "session_id": str,
        "field": str,
        "value": str
    }
)
async def update_user_profile(args: Dict[str, Any]) -> Dict:
    """
    Atualiza campo do perfil do usuário da sessão.

    Args:
        session_id: ID da sessão (para identificar o usuário)
        field: Campo a atualizar (username, profession, specialty, phone)
        value: Novo valor

    Returns:
        Confirmação da atualização
    """
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import get_db_connection

    try:
        session_id = args.get("session_id")
        field = args.get("field", "").lower().strip()
        value = args.get("value", "").strip()

        if not session_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "Erro: session_id é obrigatório"
                }],
                "isError": True
            }

        if not field or not value:
            return {
                "content": [{
                    "type": "text",
                    "text": "Erro: field e value são obrigatórios"
                }],
                "isError": True
            }

        # Campos permitidos (whitelist de segurança)
        allowed_fields = {
            "username": "username",
            "nome": "username",
            "name": "username",
            "email": "email",
            "e-mail": "email",
            "profession": "profession",
            "profissao": "profession",
            "profissão": "profession",
            "specialty": "specialty",
            "especialidade": "specialty",
            "phone": "phone_number",
            "telefone": "phone_number",
            "celular": "phone_number",
            "phone_number": "phone_number"
        }

        db_field = allowed_fields.get(field)
        if not db_field:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Erro: Campo '{field}' não pode ser atualizado. Campos permitidos: nome, email, profissão, especialidade, telefone"
                }],
                "isError": True
            }

        conn = get_db_connection()
        if not conn:
            return {
                "content": [{
                    "type": "text",
                    "text": "Erro: Falha na conexão com o banco de dados"
                }],
                "isError": True
            }

        # Primeiro, buscar o user_id da sessão
        sessions = conn.query("""
            SELECT user_id FROM chat_sessions WHERE session_id = ? LIMIT 1
        """, (session_id,))

        if not sessions:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Erro: Sessão '{session_id}' não encontrada"
                }],
                "isError": True
            }

        user_id = sessions[0]["user_id"]

        # Executar UPDATE
        logger.info(f"[update_user_profile] Updating {db_field}='{value}' for user_id={user_id}")

        conn.execute(f"""
            UPDATE users SET {db_field} = ? WHERE user_id = ?
        """, (value, user_id))

        # Buscar dados atualizados
        updated = conn.query("""
            SELECT user_id, username, email, profession, specialty, phone_number
            FROM users WHERE user_id = ? LIMIT 1
        """, (user_id,))

        user = updated[0] if updated else None

        result = {
            "success": True,
            "message": f"Campo '{field}' atualizado com sucesso para '{value}'",
            "user_id": user_id,
            "updated_field": db_field,
            "new_value": value,
            "current_profile": {
                "username": user["username"] if user else None,
                "email": user["email"] if user else None,
                "profession": user["profession"] if user else None,
                "specialty": user["specialty"] if user else None,
                "phone_number": user["phone_number"] if user else None,
            } if user else None
        }

        logger.info(f"[update_user_profile] Success: {result}")

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[update_user_profile] Error: {e}", exc_info=True)
        return {
            "content": [{
                "type": "text",
                "text": f"Erro ao atualizar perfil: {str(e)}"
            }],
            "isError": True
        }
