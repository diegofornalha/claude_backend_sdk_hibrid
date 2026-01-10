"""
Presence Service - Rastreamento de presença online

Responsável por:
- Atualizar status de presença no banco
- Listar usuários online
- Cleanup de presença stale (conexões perdidas sem logout)
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class PresenceService:
    """Gerencia presença online dos usuários"""

    def __init__(self, get_db_connection_func):
        """
        Args:
            get_db_connection_func: Função que retorna conexão do banco
        """
        self.get_db_connection = get_db_connection_func

    async def update_presence(self, user_id: int, is_online: bool):
        """
        Atualiza status de presença do usuário

        Args:
            user_id: ID do usuário
            is_online: True = conectou, False = desconectou
        """
        conn = self.get_db_connection()
        if not conn:
            return

        try:
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            if is_online:
                # Conectou: inserir ou incrementar connection_count
                # Usando upsert para SQLite
                cursor.execute("""
                    INSERT INTO user_presence (user_id, is_online, connected_at, last_seen, connection_count)
                    VALUES (?, 1, ?, ?, 1)
                    ON CONFLICT(user_id) DO UPDATE SET
                        is_online = 1,
                        last_seen = ?,
                        connection_count = connection_count + 1
                """, (user_id, now, now, now))
            else:
                # Desconectou: decrementar connection_count
                cursor.execute("""
                    UPDATE user_presence
                    SET connection_count = MAX(0, connection_count - 1),
                        is_online = CASE WHEN connection_count - 1 <= 0 THEN 0 ELSE 1 END,
                        last_seen = ?
                    WHERE user_id = ?
                """, (now, user_id))

            conn.commit()
            cursor.close()
            conn.close()

            logger.debug(f"Updated presence for user {user_id}: online={is_online}")

        except Exception as e:
            logger.error(f"Error updating presence: {e}")
            if conn:
                conn.rollback()
                conn.close()

    async def get_online_users(self, role: Optional[str] = None) -> List[Dict]:
        """
        Lista usuários online, opcionalmente filtrados por role

        Args:
            role: Filtrar por role (admin, mentorado, mentor, lead, ou None = todos)

        Returns:
            Lista de usuários online com dados
        """
        conn = self.get_db_connection()
        if not conn:
            return []

        try:
            cursor = conn.cursor(dictionary=True)

            query = """
                SELECT
                    p.user_id,
                    u.username,
                    u.role,
                    u.admin_level,
                    p.connected_at,
                    p.last_seen,
                    p.connection_count
                FROM user_presence p
                JOIN users u ON p.user_id = u.user_id
                WHERE p.is_online = 1
            """
            params = []

            if role and role != "all":
                query += " AND u.role = ?"
                params.append(role)

            query += " ORDER BY p.connected_at DESC"

            cursor.execute(query, params)
            users = cursor.fetchall()

            cursor.close()
            conn.close()

            return [
                {
                    "user_id": u["user_id"],
                    "username": u["username"],
                    "role": u["role"],
                    "admin_level": u["admin_level"],
                    "connected_at": u["connected_at"],
                    "last_seen": u["last_seen"],
                    "connection_count": u["connection_count"]
                }
                for u in users
            ]

        except Exception as e:
            logger.error(f"Error getting online users: {e}")
            if conn:
                conn.close()
            return []

    async def is_user_online(self, user_id: int) -> bool:
        """
        Verifica se usuário está online

        Args:
            user_id: ID do usuário

        Returns:
            True se online
        """
        conn = self.get_db_connection()
        if not conn:
            return False

        try:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT is_online
                FROM user_presence
                WHERE user_id = ? AND is_online = 1
                LIMIT 1
            """, (user_id,))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            return result is not None

        except Exception as e:
            logger.error(f"Error checking user online: {e}")
            if conn:
                conn.close()
            return False

    async def cleanup_stale_presence(self, timeout_minutes: int = 5):
        """
        Limpa presença de usuários desconectados sem logout (task em background)

        Marca como offline usuários sem heartbeat há mais de timeout_minutes

        Args:
            timeout_minutes: Timeout em minutos
        """
        conn = self.get_db_connection()
        if not conn:
            return

        try:
            cursor = conn.cursor()

            cutoff = (datetime.now() - timedelta(minutes=timeout_minutes)).isoformat()

            cursor.execute("""
                UPDATE user_presence
                SET is_online = 0, connection_count = 0
                WHERE last_seen < ? AND is_online = 1
            """, (cutoff,))

            count = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()

            if count > 0:
                logger.info(f"Cleaned up {count} stale presence records (timeout={timeout_minutes}min)")

        except Exception as e:
            logger.error(f"Error cleaning up stale presence: {e}")
            if conn:
                conn.rollback()
                conn.close()


# ===== SINGLETON =====

_presence_service: Optional[PresenceService] = None


def get_presence_service() -> PresenceService:
    """
    Dependency para injetar PresenceService

    Returns:
        Instância singleton do PresenceService
    """
    global _presence_service
    if _presence_service is None:
        from core.turso_database import get_db_connection
        _presence_service = PresenceService(get_db_connection)
    return _presence_service
