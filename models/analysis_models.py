"""
Analysis Models - Pydantic models para análises estruturadas (DT-SDK-003)

Estes modelos são usados para relatórios administrativos e análises de dados.
"""

from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class SessionCostEntry(BaseModel):
    """Entrada de custo por sessão"""

    session_id: str = Field(..., description="ID da sessão")
    user_id: int = Field(..., description="ID do usuário")
    username: Optional[str] = Field(None, description="Nome do usuário")
    title: Optional[str] = Field(None, description="Título da conversa")
    cost_usd: float = Field(..., ge=0, description="Custo em USD")
    message_count: int = Field(0, ge=0, description="Número de mensagens")
    created_at: Optional[str] = Field(None, description="Data de criação")


class SessionCostReport(BaseModel):
    """Relatório de custos por sessão

    Use quando admin perguntar sobre custos, gastos, consumo de API.
    """

    total_sessions: int = Field(..., ge=0, description="Total de sessões")
    total_cost_usd: float = Field(..., ge=0, description="Custo total em USD")
    average_cost_usd: float = Field(..., ge=0, description="Custo médio por sessão")

    sessions: List[SessionCostEntry] = Field(
        ...,
        description="Lista de sessões com custos detalhados"
    )

    period: Optional[str] = Field(
        None,
        description="Período do relatório (ex: 'últimos 7 dias')"
    )


class UserActivityEntry(BaseModel):
    """Atividade de um usuário"""

    user_id: int = Field(..., description="ID do usuário")
    username: str = Field(..., description="Nome do usuário")
    role: str = Field(..., description="Role do usuário")
    total_sessions: int = Field(0, description="Total de sessões")
    total_messages: int = Field(0, description="Total de mensagens")
    last_activity: Optional[str] = Field(None, description="Última atividade")


class UserActivityReport(BaseModel):
    """Relatório de atividade dos usuários

    Use quando admin perguntar sobre atividade, engajamento, usuários ativos.
    """

    total_users: int = Field(..., ge=0, description="Total de usuários")
    active_users: int = Field(..., ge=0, description="Usuários com atividade recente")

    users: List[UserActivityEntry] = Field(
        ...,
        description="Lista de usuários com atividade"
    )

    period: Optional[str] = Field(
        None,
        description="Período considerado como 'ativo'"
    )


class QueryResult(BaseModel):
    """Resultado de query estruturado"""

    query_type: str = Field(
        ...,
        description="Tipo de query (SELECT, COUNT, etc.)"
    )

    row_count: int = Field(..., ge=0, description="Número de linhas retornadas")

    columns: List[str] = Field(
        ...,
        description="Lista de colunas no resultado"
    )

    summary: str = Field(
        ...,
        description="Resumo em linguagem natural do resultado"
    )


class DataAnalysisResult(BaseModel):
    """Resultado de análise de dados

    Use quando admin pedir análises genéricas de dados.
    """

    analysis_type: str = Field(
        ...,
        description="Tipo de análise realizada"
    )

    findings: List[str] = Field(
        ...,
        description="Lista de descobertas/insights (3-5 itens)",
        min_length=1
    )

    recommendations: List[str] = Field(
        default_factory=list,
        description="Recomendações baseadas na análise"
    )

    data_summary: Optional[QueryResult] = Field(
        None,
        description="Resumo dos dados consultados"
    )
