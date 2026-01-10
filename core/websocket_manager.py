"""
WebSocket Connection Manager - Gerenciamento de conexões ativas

Responsável por:
- Rastrear todas as conexões WebSocket ativas
- Sistema de rooms/canais por sessão
- Broadcast por role (admin, mentorado, all)
- Session watchers (admins observando sessões)
- Presença online

Estrutura de dados:
- connections: {user_id: [WebSocket, ...]} # Múltiplas abas por usuário
- user_sessions: {user_id: {session_id: WebSocket}}
- session_watchers: {session_id: [admin_user_id, ...]}
- user_roles: {user_id: role} # Cache para broadcast eficiente
"""

import asyncio
import logging
from typing import Dict, List, Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Gerenciador centralizado de conexões WebSocket"""

    def __init__(self):
        self.connections: Dict[int, List[WebSocket]] = {}
        self.user_sessions: Dict[int, Dict[str, WebSocket]] = {}
        self.session_watchers: Dict[str, List[int]] = {}
        self.user_roles: Dict[int, str] = {}
        self.lock = asyncio.Lock()

    async def connect(
        self,
        user_id: int,
        websocket: WebSocket,
        session_id: Optional[str] = None
    ):
        """
        Registra nova conexão WebSocket

        Args:
            user_id: ID do usuário
            websocket: Instância do WebSocket
            session_id: ID da sessão (opcional)
        """
        async with self.lock:
            # Adicionar à lista de conexões do usuário
            if user_id not in self.connections:
                self.connections[user_id] = []
                # Cachear role do usuário
                self.user_roles[user_id] = await self._get_user_role(user_id)

            self.connections[user_id].append(websocket)

            # Mapear sessão → websocket
            if session_id:
                if user_id not in self.user_sessions:
                    self.user_sessions[user_id] = {}
                self.user_sessions[user_id][session_id] = websocket

            logger.info(f"WebSocket connected: user={user_id}, total_connections={len(self.connections[user_id])}, session={session_id}")

    async def disconnect(self, user_id: int, websocket: WebSocket):
        """
        Remove conexão WebSocket

        Args:
            user_id: ID do usuário
            websocket: Instância do WebSocket a remover
        """
        async with self.lock:
            if user_id in self.connections:
                try:
                    self.connections[user_id].remove(websocket)
                    remaining = len(self.connections[user_id])

                    # Se não tem mais conexões, limpar cache
                    if remaining == 0:
                        del self.connections[user_id]
                        if user_id in self.user_roles:
                            del self.user_roles[user_id]

                    logger.info(f"WebSocket disconnected: user={user_id}, remaining={remaining}")
                except ValueError:
                    logger.warning(f"WebSocket not found in connections for user {user_id}")

            # Limpar mapeamento de sessões
            if user_id in self.user_sessions:
                to_remove = [sid for sid, ws in self.user_sessions[user_id].items() if ws == websocket]
                for sid in to_remove:
                    del self.user_sessions[user_id][sid]
                if not self.user_sessions[user_id]:
                    del self.user_sessions[user_id]

            # Remover de watchers se for admin
            for session_id, watchers in list(self.session_watchers.items()):
                if user_id in watchers:
                    watchers.remove(user_id)
                    if not watchers:
                        del self.session_watchers[session_id]
                    logger.info(f"Admin {user_id} removed from watchers of session {session_id}")

    async def send_to_user(self, user_id: int, message: dict):
        """
        Envia mensagem para TODAS as conexões de um usuário (múltiplas abas)

        Args:
            user_id: ID do usuário
            message: Dicionário com dados da mensagem
        """
        if user_id not in self.connections:
            logger.debug(f"User {user_id} not connected, skipping message")
            return

        dead_sockets = []
        for websocket in self.connections[user_id]:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to user {user_id}: {e}")
                dead_sockets.append(websocket)

        # Cleanup de conexões mortas
        for ws in dead_sockets:
            await self.disconnect(user_id, ws)

    async def send_to_session(
        self,
        session_id: str,
        message: dict,
        exclude_user: Optional[int] = None
    ):
        """
        Envia mensagem para usuário da sessão + todos os admins observando

        Args:
            session_id: ID da sessão
            message: Mensagem a enviar
            exclude_user: User ID para excluir (opcional)
        """
        # Buscar user_id da sessão
        session_user_id = await self._get_session_user_id(session_id)
        if not session_user_id:
            logger.warning(f"Session {session_id} not found or has no user_id")
            return

        # Enviar para dono da sessão
        if session_user_id != exclude_user:
            await self.send_to_user(session_user_id, message)

        # Enviar para watchers (admins observando)
        if session_id in self.session_watchers:
            for admin_id in self.session_watchers[session_id]:
                if admin_id != exclude_user:
                    await self.send_to_user(admin_id, message)

    async def broadcast_to_role(self, role: str, message: dict):
        """
        Broadcast para todos os usuários de uma role

        Args:
            role: Role alvo (admin, mentorado, mentor, lead, ou 'all')
            message: Mensagem a enviar
        """
        target_users = [
            user_id for user_id, user_role in self.user_roles.items()
            if user_role == role or role == "all"
        ]

        logger.info(f"Broadcasting to role '{role}': {len(target_users)} users")

        # Enviar em paralelo
        tasks = [self.send_to_user(user_id, message) for user_id in target_users]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def broadcast_all(self, message: dict):
        """
        Broadcast para TODOS os usuários conectados

        Args:
            message: Mensagem a enviar
        """
        logger.info(f"Broadcasting to all: {len(self.connections)} users")

        tasks = [self.send_to_user(user_id, message) for user_id in self.connections.keys()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def add_session_watcher(self, session_id: str, admin_user_id: int):
        """
        Admin começa a observar uma sessão

        Args:
            session_id: ID da sessão
            admin_user_id: ID do admin
        """
        if session_id not in self.session_watchers:
            self.session_watchers[session_id] = []

        if admin_user_id not in self.session_watchers[session_id]:
            self.session_watchers[session_id].append(admin_user_id)
            logger.info(f"Admin {admin_user_id} now watching session {session_id}")

    async def remove_session_watcher(self, session_id: str, admin_user_id: int):
        """
        Admin para de observar uma sessão

        Args:
            session_id: ID da sessão
            admin_user_id: ID do admin
        """
        if session_id in self.session_watchers and admin_user_id in self.session_watchers[session_id]:
            self.session_watchers[session_id].remove(admin_user_id)

            if not self.session_watchers[session_id]:
                del self.session_watchers[session_id]

            logger.info(f"Admin {admin_user_id} stopped watching session {session_id}")

    def get_online_users(self) -> List[int]:
        """
        Lista user_ids online

        Returns:
            Lista de IDs de usuários conectados
        """
        return list(self.connections.keys())

    def get_session_watchers(self, session_id: str) -> List[int]:
        """
        Lista admins observando uma sessão

        Args:
            session_id: ID da sessão

        Returns:
            Lista de IDs de admins observando
        """
        return self.session_watchers.get(session_id, [])

    def is_user_online(self, user_id: int) -> bool:
        """
        Verifica se usuário está online

        Args:
            user_id: ID do usuário

        Returns:
            True se online, False caso contrário
        """
        return user_id in self.connections

    def get_connection_count(self) -> int:
        """Retorna total de conexões ativas"""
        return sum(len(sockets) for sockets in self.connections.values())

    def get_online_users_count(self) -> int:
        """Retorna total de usuários únicos online"""
        return len(self.connections)

    async def disconnect_all(self):
        """
        Desconecta todas as conexões (usado no shutdown)
        """
        logger.info("Disconnecting all WebSocket connections...")

        async with self.lock:
            total = self.get_connection_count()
            for user_id, websockets in list(self.connections.items()):
                for ws in websockets:
                    try:
                        await ws.close(code=1001, reason="Server shutdown")
                    except Exception as e:
                        logger.warning(f"Error closing websocket for user {user_id}: {e}")

            self.connections.clear()
            self.user_sessions.clear()
            self.session_watchers.clear()
            self.user_roles.clear()

            logger.info(f"All {total} connections closed")

    # ========== HELPERS PRIVADOS ==========

    async def _get_user_role(self, user_id: int) -> str:
        """
        Busca role do usuário no banco (helper privado)

        Args:
            user_id: ID do usuário

        Returns:
            Role do usuário (admin, mentorado, mentor, lead)
        """
        try:
            from core.auth import get_effective_role
            return get_effective_role(user_id)
        except Exception as e:
            logger.error(f"Error getting user role for {user_id}: {e}")
            return "mentorado"  # Fallback seguro

    async def _get_session_user_id(self, session_id: str) -> Optional[int]:
        """
        Busca user_id da sessão no banco (helper privado)

        Args:
            session_id: ID da sessão

        Returns:
            ID do usuário dono da sessão, ou None
        """
        try:
            from core.turso_database import get_db_connection
            conn = get_db_connection()
            if not conn:
                return None

            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id FROM chat_sessions WHERE session_id = ? LIMIT 1",
                (session_id,)
            )
            result = cursor.fetchone()
            conn.close()

            return result[0] if result else None

        except Exception as e:
            logger.error(f"Error getting session user_id for {session_id}: {e}")
            return None


# ===== SINGLETON =====

_connection_manager: Optional[ConnectionManager] = None


def get_connection_manager() -> ConnectionManager:
    """
    Dependency para injetar ConnectionManager nas rotas

    Returns:
        Instância singleton do ConnectionManager
    """
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
    return _connection_manager


def init_connection_manager() -> ConnectionManager:
    """
    Inicializa ConnectionManager (usado no lifespan do app.py)

    Returns:
        Nova instância do ConnectionManager
    """
    global _connection_manager
    _connection_manager = ConnectionManager()
    logger.info("ConnectionManager initialized")
    return _connection_manager
