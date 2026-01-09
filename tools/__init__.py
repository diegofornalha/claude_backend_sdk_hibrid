"""
Platform MCP Server - Unified tool server for Claude Agent SDK (White Label)

Usa Claude Code CLI local (subprocess) - SEM API KEY necessaria!

Renomeado de "crm" para "platform" para suportar White Label.
Todas as ferramentas agora usam prefixo mcp__platform__ ao invés de mcp__nanda__.
"""

from claude_agent_sdk import create_sdk_mcp_server

from .sql_tools import execute_sql_query
from .chat_tools import get_user_chat_sessions, get_session_user_info, update_user_profile
from .agentfs_tools import get_agentfs_status, get_tool_call_stats, get_recent_tool_calls


# Criar servidor MCP unificado (White Label: renomeado de "crm" para "platform")
platform_mcp_server = create_sdk_mcp_server(
    name="platform",
    version="3.0.0",  # Versão White Label
    tools=[
        # SQL tools
        execute_sql_query,

        # Chat tools
        get_user_chat_sessions,
        get_session_user_info,
        update_user_profile,

        # AgentFS tools (auditoria)
        get_agentfs_status,
        get_tool_call_stats,
        get_recent_tool_calls,
    ]
)

# Alias para retrocompatibilidade (deprecado, remover em versão futura)
nanda_mcp_server = platform_mcp_server

__all__ = [
    "platform_mcp_server",
    "nanda_mcp_server",  # Retrocompatibilidade
    "execute_sql_query",
    "get_user_chat_sessions",
    "get_session_user_info",
    "update_user_profile",
    "get_agentfs_status",
    "get_tool_call_stats",
    "get_recent_tool_calls",
]
