"""
Notification Routes - Endpoints para gerenciamento de notificações e presença

Endpoints:
- GET /api/notifications/users/online - Lista usuários online (admin)
- POST /api/notifications/notify/{user_id} - Enviar notificação (admin)
- POST /api/notifications/broadcast - Broadcast por role (admin level 0-2)
- POST /api/notifications/admin/sessions/{session_id}/watch - Observar sessão
- DELETE /api/notifications/admin/sessions/{session_id}/watch - Parar de observar
- POST /api/notifications/admin/sessions/{session_id}/message - Enviar mensagem
- GET /api/notifications/history - Histórico de notificações
- PATCH /api/notifications/{id}/read - Marcar como lida
- DELETE /api/notifications/clear-all - Marcar todas como lidas
"""

import logging
import html
from fastapi import APIRouter, HTTPException, status, Depends, Header, Query
from typing import Optional
from datetime import datetime

from core.auth import verify_token, get_effective_role, get_user_permissions
from core.websocket_manager import get_connection_manager, ConnectionManager
from core.notification_service import get_notification_service, NotificationService
from core.presence_service import get_presence_service, PresenceService
from core.session_manager import SessionManager
from core.turso_database import get_db_connection
from models.notification_models import (
    NotificationCreate,
    BroadcastCreate,
    AdminMessageCreate,
    NotificationHistoryResponse,
    OnlineUsersResponse,
    WatchSessionResponse,
    NotifyResponse,
    BroadcastResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# Session Manager
session_manager = SessionManager(get_db_connection)


# ========== HELPERS ==========

async def get_user_from_token(authorization: Optional[str] = Header(None)) -> int:
    """
    Extract user ID from JWT token

    Args:
        authorization: Authorization header com Bearer token

    Returns:
        user_id

    Raises:
        HTTPException 401 se não autenticado
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization required"
        )

    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    user_id = verify_token(token)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )

    return user_id


def require_admin(user_id: int):
    """
    Verifica se usuário é admin

    Args:
        user_id: ID do usuário

    Raises:
        HTTPException 403 se não for admin
    """
    role = get_effective_role(user_id)
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas admin pode acessar este recurso"
        )


def require_admin_level(user_id: int, max_level: int):
    """
    Verifica nível de admin

    Args:
        user_id: ID do usuário
        max_level: Nível máximo permitido (0 = owner, 1 = admin, etc)

    Raises:
        HTTPException 403 se nível insuficiente
    """
    require_admin(user_id)

    perms = get_user_permissions(user_id)
    admin_level = perms.get("adminLevel")

    if admin_level is None or admin_level > max_level:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requer admin nível 0-{max_level}"
        )


async def get_user_info(user_id: int) -> Dict:
    """Busca dados básicos do usuário"""
    conn = get_db_connection()
    if not conn:
        return {"username": f"User {user_id}", "role": "unknown"}

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT user_id, username, email, role, admin_level FROM users WHERE user_id = ? LIMIT 1",
            (user_id,)
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()

        return result if result else {"username": f"User {user_id}", "role": "unknown"}

    except Exception as e:
        logger.error(f"Error getting user info: {e}")
        if conn:
            conn.close()
        return {"username": f"User {user_id}", "role": "unknown"}


async def get_session_info(session_id: str) -> Optional[Dict]:
    """Busca dados da sessão"""
    conn = get_db_connection()
    if not conn:
        return None

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT session_id, user_id, title, created_at, updated_at FROM chat_sessions WHERE session_id = ? LIMIT 1",
            (session_id,)
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()

        return result

    except Exception as e:
        logger.error(f"Error getting session info: {e}")
        if conn:
            conn.close()
        return None


async def get_users_by_role(role: str) -> List[int]:
    """Busca user_ids por role"""
    conn = get_db_connection()
    if not conn:
        return []

    try:
        cursor = conn.cursor()

        if role == "all":
            cursor.execute("SELECT user_id FROM users")
        else:
            cursor.execute("SELECT user_id FROM users WHERE role = ?", (role,))

        results = cursor.fetchall()
        cursor.close()
        conn.close()

        return [r[0] for r in results]

    except Exception as e:
        logger.error(f"Error getting users by role: {e}")
        if conn:
            conn.close()
        return []


# ========== ENDPOINTS: PRESENÇA ONLINE ==========

@router.get("/users/online", response_model=OnlineUsersResponse)
async def get_online_users(
    role_filter: Optional[str] = Query(None, description="Filtrar por role: admin, mentorado, mentor, lead, all"),
    user_id: int = Depends(get_user_from_token),
    presence_service: PresenceService = Depends(get_presence_service)
):
    """
    Lista usuários online (apenas admin)

    Query params:
        - role_filter: Filtrar por role (opcional)

    Returns:
        {
            "success": true,
            "online_users": [...],
            "total": 15
        }
    """
    require_admin(user_id)

    online_users = await presence_service.get_online_users(role=role_filter)

    return {
        "success": True,
        "online_users": online_users,
        "total": len(online_users)
    }


# ========== ENDPOINTS: NOTIFICAÇÕES ==========

@router.post("/notify/{target_user_id}", response_model=NotifyResponse)
async def send_notification_to_user(
    target_user_id: int,
    notification: NotificationCreate,
    user_id: int = Depends(get_user_from_token),
    notification_service: NotificationService = Depends(get_notification_service),
    connection_mgr: ConnectionManager = Depends(get_connection_manager)
):
    """
    Envia notificação para usuário específico (apenas admin)

    Flow:
        1. Valida se sender é admin
        2. Salva notificação no banco
        3. Se target está online, envia via WebSocket
        4. Caso contrário, usuário verá ao conectar
    """
    require_admin(user_id)

    # Criar notificação no banco
    notification_id = await notification_service.create_notification(
        target_user_id=target_user_id,
        title=notification.title,
        body=notification.body,
        from_user_id=user_id,
        priority=notification.priority,
        action_url=notification.action_url
    )

    # Buscar dados do sender (admin)
    sender_info = await get_user_info(user_id)

    # Enviar via WebSocket se usuário online
    is_online = connection_mgr.is_user_online(target_user_id)
    if is_online:
        await connection_mgr.send_to_user(target_user_id, {
            "type": "notification",
            "notification_id": notification_id,
            "title": notification.title,
            "body": notification.body,
            "priority": notification.priority,
            "action_url": notification.action_url,
            "from_user": {
                "user_id": user_id,
                "username": sender_info.get("username")
            },
            "timestamp": datetime.now().isoformat()
        })

    return {
        "success": True,
        "notification_id": notification_id,
        "delivered": is_online
    }


@router.post("/broadcast", response_model=BroadcastResponse)
async def broadcast_notification(
    broadcast: BroadcastCreate,
    user_id: int = Depends(get_user_from_token),
    notification_service: NotificationService = Depends(get_notification_service),
    connection_mgr: ConnectionManager = Depends(get_connection_manager)
):
    """
    Broadcast para role ou todos (apenas admin nível 0-2)

    Permissions:
        - Apenas admin level 0-2 pode fazer broadcast
    """
    require_admin_level(user_id, max_level=2)

    # Buscar usuários do target_role
    target_users = await get_users_by_role(broadcast.target_role)

    # Salvar notificação para cada usuário
    notification_ids = []
    for target_user_id in target_users:
        nid = await notification_service.create_notification(
            target_user_id=target_user_id,
            title=broadcast.title,
            body=broadcast.body,
            from_user_id=user_id,
            priority=broadcast.priority
        )
        notification_ids.append(nid)

    # Enviar via WebSocket para usuários online
    await connection_mgr.broadcast_to_role(broadcast.target_role, {
        "type": "broadcast",
        "title": broadcast.title,
        "body": broadcast.body,
        "target_role": broadcast.target_role,
        "priority": broadcast.priority,
        "from": "Sistema",
        "timestamp": datetime.now().isoformat()
    })

    logger.info(f"Broadcast sent to role '{broadcast.target_role}': {len(target_users)} users, {len(notification_ids)} notifications created")

    return {
        "success": True,
        "target_role": broadcast.target_role,
        "users_notified": len(target_users),
        "notification_ids": notification_ids
    }


@router.get("/history", response_model=NotificationHistoryResponse)
async def get_notification_history(
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user_id: int = Depends(get_user_from_token),
    notification_service: NotificationService = Depends(get_notification_service)
):
    """
    Histórico de notificações do usuário

    Query params:
        - unread_only: Apenas não lidas (default: false)
        - page: Página (default: 1)
        - per_page: Itens por página (max: 100)
    """
    result = await notification_service.get_user_notifications(
        user_id=user_id,
        unread_only=unread_only,
        page=page,
        per_page=per_page
    )

    unread_count = await notification_service.get_unread_count(user_id)

    return {
        **result,
        "unread_count": unread_count
    }


@router.patch("/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    user_id: int = Depends(get_user_from_token),
    notification_service: NotificationService = Depends(get_notification_service)
):
    """Marca notificação como lida"""
    success = await notification_service.mark_as_read(notification_id, user_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notificação não encontrada ou sem permissão"
        )

    return {
        "success": True,
        "notification_id": notification_id,
        "is_read": True
    }


@router.delete("/clear-all")
async def clear_all_notifications(
    user_id: int = Depends(get_user_from_token),
    notification_service: NotificationService = Depends(get_notification_service)
):
    """Marca todas as notificações do usuário como lidas"""
    count = await notification_service.mark_all_as_read(user_id)

    return {
        "success": True,
        "marked_as_read": count
    }


# ========== ENDPOINTS: ADMIN OBSERVANDO SESSÕES ==========

@router.post("/admin/sessions/{session_id}/watch", response_model=WatchSessionResponse)
async def watch_session(
    session_id: str,
    user_id: int = Depends(get_user_from_token),
    connection_mgr: ConnectionManager = Depends(get_connection_manager)
):
    """
    Admin começa a observar uma sessão

    Flow:
        1. Valida se user é admin
        2. Verifica se sessão existe e pertence a mentorado
        3. Adiciona admin aos watchers
        4. Notifica usuário da sessão via WebSocket
        5. Admin passa a receber updates da sessão

    Security:
        - Apenas admin pode observar
        - Admin não pode observar sessão de admin de nível igual/superior
    """
    require_admin(user_id)

    # Buscar dados da sessão
    session_info = await get_session_info(session_id)
    if not session_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sessão não encontrada"
        )

    session_user_id = session_info["user_id"]
    session_user_role = get_effective_role(session_user_id)

    # Verificar hierarquia (admin não pode observar admin superior)
    if session_user_role == "admin":
        from core.auth import can_manage_user
        if not can_manage_user(user_id, session_user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Sem permissão para observar esta sessão"
            )

    # Adicionar aos watchers
    await connection_mgr.add_session_watcher(session_id, user_id)

    # Buscar dados do admin
    admin_info = await get_user_info(user_id)

    # Notificar usuário da sessão
    await connection_mgr.send_to_user(session_user_id, {
        "type": "admin_joined",
        "admin_id": user_id,
        "admin_name": admin_info.get("username", f"Admin {user_id}"),
        "session_id": session_id,
        "timestamp": datetime.now().isoformat()
    })

    # Salvar no banco (tabela session_watchers)
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO session_watchers (session_id, admin_user_id, started_watching_at)
                VALUES (?, ?, ?)
            """, (session_id, user_id, datetime.now().isoformat()))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            logger.error(f"Error saving watcher to DB: {e}")
            if conn:
                conn.rollback()
                conn.close()

    logger.info(f"Admin {user_id} started watching session {session_id}")

    return {
        "success": True,
        "session_id": session_id,
        "watching": True,
        "message": f"Agora observando sessão de {session_info.get('title', 'usuário')}"
    }


@router.delete("/admin/sessions/{session_id}/watch", response_model=WatchSessionResponse)
async def unwatch_session(
    session_id: str,
    user_id: int = Depends(get_user_from_token),
    connection_mgr: ConnectionManager = Depends(get_connection_manager)
):
    """Admin para de observar uma sessão"""
    require_admin(user_id)

    # Remover dos watchers
    await connection_mgr.remove_session_watcher(session_id, user_id)

    # Notificar usuário da sessão
    session_info = await get_session_info(session_id)
    if session_info:
        await connection_mgr.send_to_user(session_info["user_id"], {
            "type": "admin_left",
            "admin_id": user_id,
            "session_id": session_id,
            "timestamp": datetime.now().isoformat()
        })

    # Remover do banco
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM session_watchers
                WHERE session_id = ? AND admin_user_id = ?
            """, (session_id, user_id))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            logger.error(f"Error removing watcher from DB: {e}")
            if conn:
                conn.close()

    logger.info(f"Admin {user_id} stopped watching session {session_id}")

    return {
        "success": True,
        "session_id": session_id,
        "watching": False
    }


@router.post("/admin/sessions/{session_id}/message")
async def send_message_to_session(
    session_id: str,
    message: AdminMessageCreate,
    user_id: int = Depends(get_user_from_token),
    connection_mgr: ConnectionManager = Depends(get_connection_manager)
):
    """
    Admin envia mensagem direta para sessão (apenas admin)

    Flow:
        1. Valida se é admin
        2. Sanitiza conteúdo (evitar XSS)
        3. Salva mensagem no chat_messages com role='admin'
        4. Envia via WebSocket para:
           - Usuário da sessão
           - Outros admins observando (watchers)
    """
    require_admin(user_id)

    # Buscar dados da sessão
    session_info = await get_session_info(session_id)
    if not session_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sessão não encontrada"
        )

    session_user_id = session_info["user_id"]

    # Sanitizar HTML (evitar XSS)
    safe_content = html.escape(message.content)

    # Limitar tamanho
    if len(safe_content) > 4000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mensagem muito longa (max 4000 caracteres)"
        )

    # Buscar dados do admin
    admin_info = await get_user_info(user_id)

    # Salvar no banco com role='admin'
    try:
        await session_manager.save_message(
            session_id=session_id,
            user_id=user_id,  # Admin user_id
            role="admin",
            content=safe_content
        )
    except Exception as e:
        logger.error(f"Error saving admin message: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao salvar mensagem"
        )

    # Enviar via WebSocket
    message_data = {
        "type": "admin_message",
        "from_admin_id": user_id,
        "from_admin_name": admin_info.get("username", f"Admin {user_id}"),
        "content": safe_content,
        "session_id": session_id,
        "timestamp": datetime.now().isoformat()
    }

    # Enviar para usuário da sessão + watchers
    await connection_mgr.send_to_session(session_id, message_data)

    logger.info(f"Admin {user_id} sent message to session {session_id}")

    return {
        "success": True,
        "message": "Mensagem enviada com sucesso"
    }


@router.get("/admin/sessions/{session_id}/watchers")
async def get_session_watchers(
    session_id: str,
    user_id: int = Depends(get_user_from_token),
    connection_mgr: ConnectionManager = Depends(get_connection_manager)
):
    """
    Lista admins observando uma sessão (apenas admin)

    Returns:
        {
            "success": true,
            "session_id": "...",
            "watchers": [{admin_id, admin_name, watching_since}, ...],
            "total": 2
        }
    """
    require_admin(user_id)

    watcher_ids = connection_mgr.get_session_watchers(session_id)

    # Enriquecer com dados dos admins
    watchers = []
    for admin_id in watcher_ids:
        admin_info = await get_user_info(admin_id)

        # Buscar timestamp do banco
        conn = get_db_connection()
        watching_since = None
        if conn:
            try:
                cursor = conn.cursor(dictionary=True)
                cursor.execute("""
                    SELECT started_watching_at
                    FROM session_watchers
                    WHERE session_id = ? AND admin_user_id = ?
                    LIMIT 1
                """, (session_id, admin_id))
                result = cursor.fetchone()
                if result:
                    watching_since = result["started_watching_at"]
                cursor.close()
                conn.close()
            except Exception as e:
                logger.error(f"Error getting watching timestamp: {e}")
                if conn:
                    conn.close()

        watchers.append({
            "admin_id": admin_id,
            "admin_name": admin_info.get("username", f"Admin {admin_id}"),
            "watching_since": watching_since or datetime.now().isoformat()
        })

    return {
        "success": True,
        "session_id": session_id,
        "watchers": watchers,
        "total": len(watchers)
    }
