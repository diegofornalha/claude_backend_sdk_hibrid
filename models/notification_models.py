"""
Notification Models - Schemas Pydantic para notificações e presença

Schemas para validação de:
- Criação de notificações
- Broadcasts
- Mensagens de admin
- Respostas de API
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict
from datetime import datetime


class NotificationCreate(BaseModel):
    """Schema para criar nova notificação"""

    title: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Título da notificação"
    )
    body: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Corpo da mensagem"
    )
    priority: Literal["high", "medium", "low"] = Field(
        "medium",
        description="Prioridade da notificação"
    )
    action_url: Optional[str] = Field(
        None,
        description="URL de ação (opcional)"
    )


class BroadcastCreate(BaseModel):
    """Schema para criar broadcast para múltiplos usuários"""

    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=1000)
    target_role: Literal["mentorado", "mentor", "admin", "lead", "all"] = Field(
        "all",
        description="Role que vai receber o broadcast"
    )
    priority: Literal["high", "medium", "low"] = "medium"


class AdminMessageCreate(BaseModel):
    """Schema para mensagem de admin em sessão de chat"""

    content: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Conteúdo da mensagem do admin"
    )


class NotificationResponse(BaseModel):
    """Schema de resposta de notificação"""

    notification_id: int
    title: str
    body: str
    priority: str
    action_url: Optional[str]
    is_read: bool
    created_at: str
    read_at: Optional[str]
    from_user: Optional[Dict]  # {user_id, username} ou None (sistema)

    class Config:
        from_attributes = True


class OnlineUser(BaseModel):
    """Schema para usuário online"""

    user_id: int
    username: str
    role: str
    admin_level: Optional[int] = None
    connected_at: str
    last_seen: str
    connection_count: int = 1

    class Config:
        from_attributes = True


class SessionWatcher(BaseModel):
    """Schema para admin observando sessão"""

    admin_id: int
    admin_name: str
    watching_since: str

    class Config:
        from_attributes = True


class HelpRequest(BaseModel):
    """Schema para pedido de ajuda de usuário"""

    message: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Mensagem de ajuda"
    )


class UserPresenceUpdate(BaseModel):
    """Schema para atualização de presença"""

    user_id: int
    is_online: bool


class NotificationHistoryResponse(BaseModel):
    """Schema de resposta do histórico de notificações"""

    notifications: List[NotificationResponse]
    unread_count: int
    total: int
    page: int
    per_page: int


class OnlineUsersResponse(BaseModel):
    """Schema de resposta de usuários online"""

    success: bool = True
    online_users: List[OnlineUser]
    total: int


class WatchSessionResponse(BaseModel):
    """Schema de resposta ao observar sessão"""

    success: bool = True
    session_id: str
    watching: bool
    message: Optional[str] = None


class NotifyResponse(BaseModel):
    """Schema de resposta ao enviar notificação"""

    success: bool = True
    notification_id: int
    delivered: bool  # True se usuário estava online


class BroadcastResponse(BaseModel):
    """Schema de resposta ao enviar broadcast"""

    success: bool = True
    target_role: str
    users_notified: int
    notification_ids: List[int]
