"""
Notification Service - Gerenciamento de notificações

Responsável por:
- CRUD de notificações no banco
- Histórico de notificações por usuário
- Contador de não lidas
- Marcação de leitura
- Cleanup de notificações antigas
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class NotificationService:
    """Gerencia notificações com persistência no banco"""

    def __init__(self, get_db_connection_func):
        """
        Args:
            get_db_connection_func: Função que retorna conexão do banco
        """
        self.get_db_connection = get_db_connection_func

    async def create_notification(
        self,
        target_user_id: int,
        title: str,
        body: str,
        from_user_id: Optional[int] = None,
        priority: str = "medium",
        action_url: Optional[str] = None
    ) -> int:
        """
        Cria notificação no banco

        Args:
            target_user_id: Quem vai receber
            title: Título da notificação
            body: Corpo da mensagem
            from_user_id: Quem enviou (None = sistema)
            priority: high, medium, low
            action_url: URL de ação (opcional)

        Returns:
            notification_id
        """
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")

        try:
            cursor = conn.cursor()

            query = """
                INSERT INTO notifications
                (target_user_id, from_user_id, title, body, priority, action_url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """

            cursor.execute(query, (
                target_user_id,
                from_user_id,
                title,
                body,
                priority,
                action_url,
                datetime.now().isoformat()
            ))

            notification_id = cursor.lastrowid
            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"Created notification {notification_id} for user {target_user_id}")
            return notification_id

        except Exception as e:
            logger.error(f"Error creating notification: {e}")
            if conn:
                conn.rollback()
                conn.close()
            raise

    async def mark_as_read(self, notification_id: int, user_id: int) -> bool:
        """
        Marca notificação como lida

        Args:
            notification_id: ID da notificação
            user_id: ID do usuário (para segurança)

        Returns:
            True se marcada com sucesso
        """
        conn = self.get_db_connection()
        if not conn:
            return False

        try:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE notifications
                SET is_read = 1, read_at = ?
                WHERE notification_id = ? AND target_user_id = ? AND is_read = 0
            """, (datetime.now().isoformat(), notification_id, user_id))

            rows_affected = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()

            success = rows_affected > 0
            if success:
                logger.debug(f"Marked notification {notification_id} as read")

            return success

        except Exception as e:
            logger.error(f"Error marking notification as read: {e}")
            if conn:
                conn.close()
            return False

    async def mark_all_as_read(self, user_id: int) -> int:
        """
        Marca todas as notificações do usuário como lidas

        Args:
            user_id: ID do usuário

        Returns:
            Número de notificações marcadas
        """
        conn = self.get_db_connection()
        if not conn:
            return 0

        try:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE notifications
                SET is_read = 1, read_at = ?
                WHERE target_user_id = ? AND is_read = 0
            """, (datetime.now().isoformat(), user_id))

            count = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"Marked {count} notifications as read for user {user_id}")
            return count

        except Exception as e:
            logger.error(f"Error marking all notifications as read: {e}")
            if conn:
                conn.close()
            return 0

    async def get_user_notifications(
        self,
        user_id: int,
        unread_only: bool = False,
        page: int = 1,
        per_page: int = 20
    ) -> Dict:
        """
        Lista notificações do usuário com paginação

        Args:
            user_id: ID do usuário
            unread_only: Filtrar apenas não lidas
            page: Número da página
            per_page: Itens por página

        Returns:
            {notifications: [...], total: N, page: N, per_page: N}
        """
        conn = self.get_db_connection()
        if not conn:
            return {"notifications": [], "total": 0, "page": page, "per_page": per_page}

        try:
            cursor = conn.cursor(dictionary=True)

            # Contar total
            count_query = "SELECT COUNT(*) as total FROM notifications WHERE target_user_id = ?"
            count_params = [user_id]

            if unread_only:
                count_query += " AND is_read = 0"

            cursor.execute(count_query, count_params)
            count_result = cursor.fetchone()
            total = count_result["total"] if count_result else 0

            # Buscar notificações
            offset = (page - 1) * per_page
            query = """
                SELECT
                    n.notification_id,
                    n.title,
                    n.body,
                    n.priority,
                    n.action_url,
                    n.is_read,
                    n.created_at,
                    n.read_at,
                    n.from_user_id,
                    u.username as from_username
                FROM notifications n
                LEFT JOIN users u ON n.from_user_id = u.user_id
                WHERE n.target_user_id = ?
            """
            params = [user_id]

            if unread_only:
                query += " AND n.is_read = 0"

            query += " ORDER BY n.created_at DESC LIMIT ? OFFSET ?"
            params.extend([per_page, offset])

            cursor.execute(query, params)
            notifications = cursor.fetchall()

            cursor.close()
            conn.close()

            # Formatar resposta
            formatted = []
            for notif in notifications:
                formatted.append({
                    "notification_id": notif["notification_id"],
                    "title": notif["title"],
                    "body": notif["body"],
                    "priority": notif["priority"],
                    "action_url": notif["action_url"],
                    "is_read": bool(notif["is_read"]),
                    "created_at": notif["created_at"],
                    "read_at": notif["read_at"],
                    "from_user": {
                        "user_id": notif["from_user_id"],
                        "username": notif["from_username"]
                    } if notif["from_user_id"] else None
                })

            return {
                "notifications": formatted,
                "total": total,
                "page": page,
                "per_page": per_page
            }

        except Exception as e:
            logger.error(f"Error getting user notifications: {e}")
            if conn:
                conn.close()
            return {"notifications": [], "total": 0, "page": page, "per_page": per_page}

    async def get_unread_count(self, user_id: int) -> int:
        """
        Conta notificações não lidas

        Args:
            user_id: ID do usuário

        Returns:
            Número de notificações não lidas
        """
        conn = self.get_db_connection()
        if not conn:
            return 0

        try:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT COUNT(*) as count
                FROM notifications
                WHERE target_user_id = ? AND is_read = 0
            """, (user_id,))

            result = cursor.fetchone()
            count = result[0] if result else 0

            cursor.close()
            conn.close()

            return count

        except Exception as e:
            logger.error(f"Error getting unread count: {e}")
            if conn:
                conn.close()
            return 0

    async def delete_old_notifications(self, days: int = 30) -> int:
        """
        Limpa notificações antigas (task em background)

        Args:
            days: Deletar notificações com mais de N dias

        Returns:
            Número de notificações deletadas
        """
        conn = self.get_db_connection()
        if not conn:
            return 0

        try:
            cursor = conn.cursor()

            cutoff = (datetime.now() - timedelta(days=days)).isoformat()

            cursor.execute("""
                DELETE FROM notifications
                WHERE created_at < ? AND is_read = 1
            """, (cutoff,))

            deleted = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()

            if deleted > 0:
                logger.info(f"Deleted {deleted} old notifications (>{days} days)")

            return deleted

        except Exception as e:
            logger.error(f"Error deleting old notifications: {e}")
            if conn:
                conn.rollback()
                conn.close()
            return 0


# ===== SINGLETON =====

_notification_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    """
    Dependency para injetar NotificationService

    Returns:
        Instância singleton do NotificationService
    """
    global _notification_service
    if _notification_service is None:
        from core.turso_database import get_db_connection
        _notification_service = NotificationService(get_db_connection)
    return _notification_service
