"""
AgentFS Tools - Ferramentas MCP para consultar o AgentFS

ARQUITETURA DE ISOLAMENTO:
- Cada usuário tem seu próprio banco: .agentfs/user-{id}.db
- Estas tools EXIGEM user_id para acessar o banco correto
- Admin pode passar user_id de outro usuário para auditoria

Fornece acesso a:
- Estatísticas de tool calls (auditoria)
- Status do AgentFS
- Tool calls recentes
"""

import time
import logging
from typing import Dict, Any

from claude_agent_sdk import tool

logger = logging.getLogger(__name__)


@tool(
    "get_agentfs_status",
    "Verifica o status do AgentFS para um usuário específico. Retorna info sobre conexão e estatísticas.",
    {
        "user_id": {
            "type": "integer",
            "description": "ID do usuário para verificar status (obrigatório)",
            "required": True
        }
    }
)
async def get_agentfs_status(args: Dict[str, Any]) -> Dict:
    """
    Verifica o status do AgentFS para um usuário específico.

    Retorna informações sobre:
    - Se o AgentFS está conectado
    - ID do agente (user-{id})
    - Caminho do banco do usuário
    - Estatísticas de tools do usuário
    """
    user_id = args.get("user_id")
    if user_id is None:
        return {
            "content": [{"type": "text", "text": "Erro: user_id é obrigatório"}]
        }

    try:
        from core.agentfs_client import get_agentfs

        agentfs = await get_agentfs(user_id=user_id)

        # Obter estatísticas de tools do usuário
        try:
            tool_stats = await agentfs.tool_stats()
            stats_count = len(tool_stats) if tool_stats else 0
        except Exception:
            tool_stats = []
            stats_count = 0

        result = {
            "success": True,
            "status": "connected",
            "user_id": user_id,
            "agent_id": agentfs._agent_id,
            "database_path": f".agentfs/user-{user_id}.db",
            "sync_enabled": bool(agentfs._sync_url),
            "tools_tracked": stats_count,
            "message": f"AgentFS do usuário {user_id} está funcionando!"
        }

        return {
            "content": [{"type": "text", "text": str(result)}]
        }

    except ImportError:
        return {
            "content": [{"type": "text", "text": "AgentFS não disponível neste ambiente"}]
        }
    except Exception as e:
        logger.error(f"AgentFS status error for user {user_id}: {e}")
        return {
            "content": [{"type": "text", "text": f"Erro ao verificar AgentFS: {e}"}]
        }


@tool(
    "get_tool_call_stats",
    "Obtém estatísticas de uso de ferramentas de um usuário específico (auditoria).",
    {
        "user_id": {
            "type": "integer",
            "description": "ID do usuário para obter estatísticas (obrigatório)",
            "required": True
        }
    }
)
async def get_tool_call_stats(args: Dict[str, Any]) -> Dict:
    """
    Obtém estatísticas de uso de ferramentas de um usuário específico.

    Retorna:
    - Total de chamadas
    - Chamadas com sucesso
    - Chamadas com erro
    - Ferramentas mais usadas
    - Taxa de sucesso
    """
    user_id = args.get("user_id")
    if user_id is None:
        return {
            "content": [{"type": "text", "text": "Erro: user_id é obrigatório"}]
        }

    try:
        from core.agentfs_client import get_agentfs

        agentfs = await get_agentfs(user_id=user_id)
        stats = await agentfs.tool_stats()

        if not stats:
            result = {
                "success": True,
                "user_id": user_id,
                "total_calls": 0,
                "success_calls": 0,
                "error_calls": 0,
                "success_rate": 0,
                "tools": [],
                "message": f"Nenhuma chamada de ferramenta registrada para usuário {user_id}."
            }
            return {
                "content": [{"type": "text", "text": str(result)}]
            }

        # Processar estatísticas (stats é lista de ToolCallStats)
        total_calls = 0
        success_calls = 0
        error_calls = 0
        tools_summary = {}

        for stat in stats:
            # ToolCallStats tem atributos: name, total_calls, successful, failed, avg_duration_ms
            tool_name = getattr(stat, 'name', 'unknown')
            calls = getattr(stat, 'total_calls', 0)
            successes = getattr(stat, 'successful', 0)
            errors = getattr(stat, 'failed', 0)
            avg_duration = getattr(stat, 'avg_duration_ms', 0)

            total_calls += calls
            success_calls += successes
            error_calls += errors

            tools_summary[tool_name] = {
                "calls": calls,
                "successes": successes,
                "errors": errors,
                "avg_duration_ms": avg_duration,
                "success_rate": round((successes / calls * 100) if calls > 0 else 0, 1)
            }

        # Ordenar por mais usadas
        sorted_tools = sorted(
            tools_summary.items(),
            key=lambda x: x[1]["calls"],
            reverse=True
        )

        success_rate = round((success_calls / total_calls * 100) if total_calls > 0 else 0, 1)

        result = {
            "success": True,
            "user_id": user_id,
            "total_calls": total_calls,
            "success_calls": success_calls,
            "error_calls": error_calls,
            "success_rate": success_rate,
            "tools": [
                {"name": name, **data}
                for name, data in sorted_tools[:10]  # Top 10 tools
            ],
            "message": f"Usuário {user_id}: {total_calls} chamadas com {success_rate}% de sucesso."
        }

        return {
            "content": [{"type": "text", "text": str(result)}]
        }

    except ImportError:
        return {
            "content": [{"type": "text", "text": "AgentFS não disponível"}]
        }
    except Exception as e:
        logger.error(f"Tool stats error for user {user_id}: {e}")
        return {
            "content": [{"type": "text", "text": f"Erro ao obter estatísticas: {e}"}]
        }


@tool(
    "get_recent_tool_calls",
    "Obtém as chamadas de ferramentas mais recentes de um usuário (auditoria).",
    {
        "user_id": {
            "type": "integer",
            "description": "ID do usuário para obter chamadas recentes (obrigatório)",
            "required": True
        },
        "limit": {
            "type": "integer",
            "description": "Número máximo de chamadas a retornar (default: 10)",
            "required": False
        },
        "hours": {
            "type": "integer",
            "description": "Buscar chamadas das últimas N horas (default: 24)",
            "required": False
        }
    }
)
async def get_recent_tool_calls(args: Dict[str, Any]) -> Dict:
    """
    Obtém as chamadas de ferramentas mais recentes de um usuário.

    Usa a tabela nativa tool_calls para buscar as chamadas.
    """
    user_id = args.get("user_id")
    if user_id is None:
        return {
            "content": [{"type": "text", "text": "Erro: user_id é obrigatório"}]
        }

    limit = args.get("limit", 10)
    hours = args.get("hours", 24)

    try:
        from core.agentfs_client import get_agentfs

        agentfs = await get_agentfs(user_id=user_id)
        recent_calls = await agentfs.tool_get_recent(limit=limit, hours=hours)

        if not recent_calls:
            result = {
                "success": True,
                "user_id": user_id,
                "count": 0,
                "calls": [],
                "message": f"Nenhuma chamada nas últimas {hours}h para usuário {user_id}."
            }
            return {
                "content": [{"type": "text", "text": str(result)}]
            }

        # Formatar chamadas (ToolCall objects)
        formatted_calls = []
        for call in recent_calls:
            formatted_calls.append({
                "id": getattr(call, 'id', None),
                "tool": getattr(call, 'name', 'unknown'),
                "status": getattr(call, 'status', 'unknown'),
                "duration_ms": getattr(call, 'duration_ms', 0),
                "started_at": getattr(call, 'started_at', 0),
                "error": getattr(call, 'error', None)
            })

        result = {
            "success": True,
            "user_id": user_id,
            "count": len(formatted_calls),
            "calls": formatted_calls,
            "message": f"{len(formatted_calls)} chamadas recentes para usuário {user_id}."
        }

        return {
            "content": [{"type": "text", "text": str(result)}]
        }

    except ImportError:
        return {
            "content": [{"type": "text", "text": "AgentFS não disponível"}]
        }
    except Exception as e:
        logger.error(f"Recent calls error for user {user_id}: {e}")
        return {
            "content": [{"type": "text", "text": f"Erro ao obter chamadas: {e}"}]
        }


# ==============================================================================
# SELF-AWARENESS TOOLS - Monitoramento do sistema (apenas admin)
# ==============================================================================

@tool(
    "get_system_health",
    "Visão geral da saúde do sistema AgentFS. Mostra usuários, storage, taxa de sucesso e problemas detectados.",
    {
        "hours": {
            "type": "integer",
            "description": "Período de análise em horas (default: 24)",
            "required": False
        }
    }
)
async def get_system_health(args: Dict[str, Any]) -> Dict:
    """
    Retorna visão geral da saúde do sistema AgentFS.

    Inclui:
    - Total de usuários com AgentFS
    - Usuários ativos no período
    - Storage total usado
    - Tool calls e taxa de sucesso
    - Problemas detectados automaticamente
    """
    hours = args.get("hours", 24)

    try:
        from core.agentfs_manager import get_agentfs_manager

        manager = await get_agentfs_manager()
        health = await manager.get_system_health_data(hours=hours)
        problems = await manager.get_problematic_tools(hours=hours)

        # Formatar resposta amigável
        response = f"""📊 **SAÚDE DO SISTEMA AGENTFS**

**Período analisado:** últimas {hours}h

---

**👥 Usuários:**
- Total com AgentFS: {health['total_users']}
- Ativos no período: {health['active_users']}

**💾 Storage:**
- Total usado: {health['total_storage_mb']:.2f} MB

**🔧 Tool Calls ({hours}h):**
- Total: {health['total_calls']}
- Sucesso: {health['success_calls']} ({health['success_rate']:.0%})
- Erros: {health['error_calls']}
- Tempo médio: {health['avg_duration_ms']:.0f}ms

---

**⚠️ Problemas Detectados:** {len(problems)}
"""
        if problems:
            for p in problems:
                response += f"\n- **{p['tool']}**: {p['issue']}"
        else:
            response += "\n✅ Nenhum problema detectado!"

        return {
            "content": [{"type": "text", "text": response}]
        }

    except Exception as e:
        logger.error(f"System health error: {e}")
        return {
            "content": [{"type": "text", "text": f"Erro ao obter saúde do sistema: {e}"}]
        }


@tool(
    "get_tool_problems",
    "Detecta ferramentas com alta taxa de erro ou lentidão. Útil para diagnóstico de problemas.",
    {
        "hours": {
            "type": "integer",
            "description": "Período de análise em horas (default: 24)",
            "required": False
        },
        "error_threshold": {
            "type": "number",
            "description": "Taxa de erro para alertar, em decimal (default: 0.1 = 10%)",
            "required": False
        },
        "slow_threshold_ms": {
            "type": "integer",
            "description": "Tempo em ms para considerar lento (default: 5000)",
            "required": False
        }
    }
)
async def get_tool_problems(args: Dict[str, Any]) -> Dict:
    """
    Identifica ferramentas problemáticas.

    Detecta:
    - Tools com taxa de erro acima do threshold
    - Tools com tempo de resposta muito alto
    - Recomendações de ação
    """
    hours = args.get("hours", 24)
    error_threshold = args.get("error_threshold", 0.1)
    slow_threshold_ms = args.get("slow_threshold_ms", 5000)

    try:
        from core.agentfs_manager import get_agentfs_manager

        manager = await get_agentfs_manager()
        problems = await manager.get_problematic_tools(
            hours=hours,
            error_threshold=error_threshold,
            slow_threshold_ms=slow_threshold_ms
        )

        if not problems:
            response = f"""✅ **NENHUM PROBLEMA DETECTADO**

Análise das últimas {hours}h:
- Threshold de erro: {error_threshold:.0%}
- Threshold de lentidão: {slow_threshold_ms}ms

Todas as ferramentas estão operando normalmente!
"""
        else:
            response = f"""⚠️ **{len(problems)} FERRAMENTA(S) COM PROBLEMAS**

Análise das últimas {hours}h:
- Threshold de erro: {error_threshold:.0%}
- Threshold de lentidão: {slow_threshold_ms}ms

---
"""
            for i, p in enumerate(problems, 1):
                response += f"""
**{i}. {p['tool']}**
- Chamadas: {p['total_calls']}
- Taxa de erro: {p['error_rate']:.0%}
- Tempo médio: {p['avg_duration_ms']:.0f}ms
- Problemas: {p['issue']}
"""
            response += """
---

**💡 Recomendações:**
- Tools com erro alto: verificar logs, testar manualmente
- Tools lentas: otimizar ou usar cache
"""

        return {
            "content": [{"type": "text", "text": response}]
        }

    except Exception as e:
        logger.error(f"Tool problems error: {e}")
        return {
            "content": [{"type": "text", "text": f"Erro ao detectar problemas: {e}"}]
        }


@tool(
    "get_user_activity",
    "Mostra ranking de atividade dos usuários no AgentFS. Útil para entender uso do sistema.",
    {
        "hours": {
            "type": "integer",
            "description": "Período de análise em horas (default: 24)",
            "required": False
        },
        "top_n": {
            "type": "integer",
            "description": "Número de usuários a mostrar (default: 10)",
            "required": False
        }
    }
)
async def get_user_activity(args: Dict[str, Any]) -> Dict:
    """
    Ranking de atividade por usuário.

    Mostra:
    - Usuários mais ativos
    - Tool calls por usuário
    - Taxa de sucesso individual
    - Ferramentas mais usadas por cada um
    """
    hours = args.get("hours", 24)
    top_n = args.get("top_n", 10)

    try:
        from core.agentfs_manager import get_agentfs_manager
        from datetime import datetime

        manager = await get_agentfs_manager()
        activity = await manager.get_user_activity_data(hours=hours, top_n=top_n)

        if not activity:
            response = f"""📊 **ATIVIDADE DE USUÁRIOS**

Nenhum usuário ativo nas últimas {hours}h.
"""
        else:
            response = f"""📊 **TOP {len(activity)} USUÁRIOS ATIVOS** (últimas {hours}h)

"""
            for i, user in enumerate(activity, 1):
                # Formatar última atividade
                last_time = datetime.fromtimestamp(user['last_activity']).strftime('%H:%M') if user['last_activity'] else 'N/A'

                # Top tools do usuário
                top_tools_str = ", ".join([f"{t['name'].split('__')[-1]}({t['count']})" for t in user['top_tools'][:2]]) if user['top_tools'] else "N/A"

                response += f"""**{i}. User {user['user_id']}**
   - Calls: {user['total_calls']} ({user['success_rate']:.0%} sucesso)
   - Última atividade: {last_time}
   - DB: {user['db_size_mb']:.2f} MB
   - Top tools: {top_tools_str}

"""

        return {
            "content": [{"type": "text", "text": response}]
        }

    except Exception as e:
        logger.error(f"User activity error: {e}")
        return {
            "content": [{"type": "text", "text": f"Erro ao obter atividade: {e}"}]
        }


@tool(
    "get_storage_report",
    "Relatório detalhado de uso de storage do AgentFS. Mostra espaço usado por usuário e candidatos a cleanup.",
    {}
)
async def get_storage_report(args: Dict[str, Any]) -> Dict:
    """
    Relatório de uso de storage.

    Inclui:
    - Storage total
    - Top usuários por tamanho de DB
    - Usuários que precisam de cleanup
    - Recomendações
    """
    try:
        from core.agentfs_manager import get_agentfs_manager

        manager = await get_agentfs_manager()
        report = await manager.get_storage_report()

        response = f"""💾 **RELATÓRIO DE STORAGE AGENTFS**

**Total:** {report['total_storage_mb']:.2f} MB
**Usuários:** {report['total_users']}

---

**📦 Storage por Usuário:**
"""
        for user in report['users'][:10]:  # Top 10
            cleanup_flag = " ⚠️" if user['needs_cleanup'] else ""
            response += f"""
- **User {user['user_id']}**: {user['size_mb']:.2f} MB{cleanup_flag}
  - Tool calls: {user['tool_calls_total']}
  - KV entries: {user['kv_total']}
"""

        if report['candidates_cleanup']:
            response += f"""
---

**🧹 Candidatos a Cleanup:** {len(report['candidates_cleanup'])}
"""
            for u in report['candidates_cleanup'][:5]:
                response += f"- User {u['user_id']}: {u['size_mb']:.2f} MB\n"
        else:
            response += """
---

✅ Nenhum usuário precisa de cleanup imediato.
"""

        response += """
---

**💡 Política de Retenção:**
- tool_calls: 30 dias
- kv_store: 90 dias
- Cleanup automático: 3:30 AM diário
"""

        return {
            "content": [{"type": "text", "text": response}]
        }

    except Exception as e:
        logger.error(f"Storage report error: {e}")
        return {
            "content": [{"type": "text", "text": f"Erro ao gerar relatório: {e}"}]
        }
