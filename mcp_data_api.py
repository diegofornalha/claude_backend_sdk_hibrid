#!/usr/bin/env python3
"""
MCP Server para API do Nanda
Expõe os endpoints da API REST do backend Nanda como ferramentas MCP
"""

import httpx
from mcp.server.fastmcp import FastMCP

# Configuração
API_BASE_URL = "http://localhost:8234"

mcp = FastMCP("crm-api")

# Cliente HTTP reutilizável
def get_client():
    return httpx.Client(base_url=API_BASE_URL, timeout=30.0)


# ==================== HEALTH ====================

@mcp.tool()
def health_check() -> dict:
    """Verifica se a API do Nanda está online e saudável."""
    with get_client() as client:
        response = client.get("/health")
        return response.json()


# ==================== REPORTS ====================

@mcp.tool()
def list_reports(limit: int = 20, offset: int = 0, status: str = None, waste_type: str = None) -> dict:
    """
    Lista relatórios de resíduos com paginação e filtros opcionais.

    Args:
        limit: Número máximo de resultados (default 20)
        offset: Offset para paginação (default 0)
        status: Filtrar por status (pending, verified, resolved)
        waste_type: Filtrar por tipo de resíduo
    """
    params = {"limit": limit, "offset": offset}
    if status:
        params["status"] = status
    if waste_type:
        params["waste_type"] = waste_type

    with get_client() as client:
        response = client.get("/api/reports", params=params)
        return response.json()


@mcp.tool()
def get_report(report_id: int) -> dict:
    """
    Obtém detalhes de um relatório específico.

    Args:
        report_id: ID do relatório
    """
    with get_client() as client:
        response = client.get(f"/api/reports/{report_id}")
        return response.json()


@mcp.tool()
def get_nearby_reports(latitude: float, longitude: float, radius_km: float = 5.0) -> dict:
    """
    Busca relatórios próximos a uma localização.

    Args:
        latitude: Latitude do ponto central
        longitude: Longitude do ponto central
        radius_km: Raio de busca em km (default 5km)
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "radius_km": radius_km
    }
    with get_client() as client:
        response = client.get("/api/reports/nearby", params=params)
        return response.json()


# ==================== HOTSPOTS ====================

@mcp.tool()
def list_hotspots(status: str = None, limit: int = 20) -> dict:
    """
    Lista hotspots de resíduos (áreas com alta concentração).

    Args:
        status: Filtrar por status (active, monitoring, resolved)
        limit: Número máximo de resultados
    """
    params = {"limit": limit}
    if status:
        params["status"] = status

    with get_client() as client:
        response = client.get("/api/hotspots", params=params)
        return response.json()


@mcp.tool()
def get_hotspot_reports(hotspot_id: int) -> dict:
    """
    Obtém relatórios associados a um hotspot.

    Args:
        hotspot_id: ID do hotspot
    """
    with get_client() as client:
        response = client.get(f"/api/hotspots/{hotspot_id}/reports")
        return response.json()


# ==================== DASHBOARD ====================

@mcp.tool()
def get_dashboard_statistics() -> dict:
    """
    Obtém estatísticas gerais do dashboard.
    Retorna contagens de relatórios, hotspots, tendências, etc.
    """
    with get_client() as client:
        response = client.get("/api/dashboard/statistics")
        return response.json()


# ==================== WASTE TYPES ====================

@mcp.tool()
def list_waste_types() -> dict:
    """Lista todos os tipos de resíduos cadastrados no sistema."""
    with get_client() as client:
        response = client.get("/api/waste-types")
        return response.json()


# ==================== CHAT SESSIONS ====================

@mcp.tool()
def list_chat_sessions(token: str) -> dict:
    """
    Lista sessões de chat do usuário.

    Args:
        token: JWT token de autenticação
    """
    headers = {"Authorization": f"Bearer {token}"}
    with get_client() as client:
        response = client.get("/api/chat/sessions", headers=headers)
        return response.json()


@mcp.tool()
def get_chat_messages(session_id: str, token: str) -> dict:
    """
    Obtém mensagens de uma sessão de chat.

    Args:
        session_id: ID da sessão
        token: JWT token de autenticação
    """
    headers = {"Authorization": f"Bearer {token}"}
    with get_client() as client:
        response = client.get(f"/api/chat/sessions/{session_id}/messages", headers=headers)
        return response.json()


# ==================== USERS ====================

@mcp.tool()
def get_user(user_id: int, token: str) -> dict:
    """
    Obtém dados de um usuário.

    Args:
        user_id: ID do usuário
        token: JWT token de autenticação
    """
    headers = {"Authorization": f"Bearer {token}"}
    with get_client() as client:
        response = client.get(f"/api/users/{user_id}", headers=headers)
        return response.json()


# ==================== QUEUE ====================

@mcp.tool()
def get_process_queue() -> dict:
    """
    Obtém status da fila de processamento.
    Mostra relatórios pendentes de análise.
    """
    with get_client() as client:
        response = client.get("/api/process-queue")
        return response.json()


if __name__ == "__main__":
    mcp.run()
