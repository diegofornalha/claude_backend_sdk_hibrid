"""
Hooks para Claude Agent SDK

Funções invocadas em pontos específicos do loop do agente para:
- Validar operações antes de executar
- Auditar ferramentas utilizadas (AgentFS Tool Tracking)
- Parar execução em erros críticos
"""

import logging
import time
from typing import Optional
from claude_agent_sdk.types import HookInput, HookContext, HookJSONOutput

logger = logging.getLogger(__name__)

# Cache de tool_call_ids para rastrear início/fim
_tool_call_cache: dict = {}


async def validate_sql_query(
    input_data: HookInput,
    tool_use_id: Optional[str],
    context: HookContext
) -> HookJSONOutput:
    """
    Hook PreToolUse para validar queries SQL antes de executar.

    Bloqueia:
    - Operações perigosas (DROP, TRUNCATE, ALTER)
    - DELETE/UPDATE sem WHERE
    - SQL Injection patterns
    - Queries muito longas (>5000 caracteres)
    - Multiple statements (SQL injection comum)
    """
    tool_name = input_data.get("tool_name")
    tool_input = input_data.get("tool_input", {})

    # Só processar se for a ferramenta SQL (White Label: mcp__platform__)
    if tool_name != "mcp__platform__execute_sql_query":
        return {}

    query = tool_input.get("query", "").strip()
    query_upper = query.upper()

    if not query:
        return {}

    # 1. Verificar tamanho da query (proteção contra abuso)
    if len(query) > 5000:
        logger.warning(f"Blocked oversized SQL query: {len(query)} chars")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "🚫 Query muito longa (>5000 caracteres). Divida em múltiplas queries."
            }
        }

    # 2. Verificar múltiplos statements (SQL injection comum)
    # Permitir apenas 1 statement (sem ; no meio da query)
    if query.count(';') > 1 or (';' in query and not query.strip().endswith(';')):
        logger.warning(f"Blocked multi-statement SQL: {query[:100]}")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "🚫 Múltiplos statements não são permitidos. Execute uma query por vez."
            }
        }

    # 3. Lista de operações perigosas (verificar como palavras completas)
    import re
    dangerous_operations = {
        "DROP": "DROP não é permitido",
        "TRUNCATE": "TRUNCATE não é permitido",
        "ALTER": "ALTER não é permitido para proteger estrutura do banco",
        "GRANT": "GRANT não é permitido",
        "REVOKE": "REVOKE não é permitido",
        "CREATE USER": "CREATE USER não é permitido",
        "EXEC": "EXEC não é permitido",
        "EXECUTE": "EXECUTE não é permitido",
    }

    # Verificar operações perigosas (como palavras completas, não substrings)
    for operation, reason in dangerous_operations.items():
        # Usar regex para verificar palavra completa (\b = word boundary)
        pattern = rf'\b{operation}\b'
        if re.search(pattern, query_upper):
            logger.warning(f"Blocked SQL query with {operation}: {query[:100]}")
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"🚫 {reason}. Use apenas SELECT para consultas."
                }
            }

    # 4. Verificar SQL Injection patterns comuns
    injection_patterns = [
        "UNION SELECT",
        "OR 1=1",
        "OR '1'='1",
        "OR \"1\"=\"1\"",
        "--",  # SQL comment
        "/*",  # Multi-line comment start
        "XP_",  # xp_cmdshell e outras procedures perigosas
        "SP_",  # System stored procedures
    ]

    for pattern in injection_patterns:
        if pattern in query_upper:
            logger.warning(f"Blocked SQL injection pattern '{pattern}': {query[:100]}")
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"🚫 Pattern suspeito detectado: '{pattern}'. Possível SQL injection."
                }
            }

    # 5. Verificar DELETE sem WHERE (perigoso)
    if "DELETE" in query_upper and "WHERE" not in query_upper:
        logger.warning(f"Blocked DELETE without WHERE: {query[:100]}")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "🚫 DELETE sem WHERE pode apagar todos os registros. "
                                          "Adicione uma cláusula WHERE para segurança."
            }
        }

    # 6. Verificar UPDATE sem WHERE (perigoso)
    if "UPDATE" in query_upper and "WHERE" not in query_upper:
        logger.warning(f"Blocked UPDATE without WHERE: {query[:100]}")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "🚫 UPDATE sem WHERE pode alterar todos os registros. "
                                          "Adicione uma cláusula WHERE para segurança."
            }
        }

    # 7. Verificar INSERT em tabelas sensíveis (opcional, descomente se necessário)
    # sensitive_tables = ["users", "passwords", "tokens", "config"]
    # if "INSERT INTO" in query_upper:
    #     for table in sensitive_tables:
    #         if table.upper() in query_upper:
    #             logger.warning(f"Blocked INSERT into sensitive table {table}: {query[:100]}")
    #             return {
    #                 "hookSpecificOutput": {
    #                     "hookEventName": "PreToolUse",
    #                     "permissionDecision": "deny",
    #                     "permissionDecisionReason": f"🚫 INSERT em tabela sensível '{table}' não é permitido."
    #                 }
    #             }

    # Query aprovada
    logger.info(f"SQL query approved: {query[:100]}")
    return {}


def _extract_user_id(input_data: HookInput, context: HookContext) -> Optional[int]:
    """
    Extrai user_id do contexto da chamada.

    Tenta extrair de múltiplas fontes:
    1. tool_input.user_id (tools que recebem user_id como parâmetro)
    2. conversation_id no formato "user_{id}_session_{uuid}"

    Args:
        input_data: Dados da chamada da ferramenta
        context: Contexto do hook

    Returns:
        user_id ou None se não encontrado
    """
    user_id = None

    # 1. Tentar extrair do tool_input
    tool_input = input_data.get("tool_input", {})
    if isinstance(tool_input, dict) and "user_id" in tool_input:
        try:
            user_id = int(tool_input["user_id"])
            return user_id
        except (ValueError, TypeError):
            pass

    # 2. Tentar extrair do conversation_id
    if context:
        conv_id = getattr(context, 'conversation_id', None)
        if conv_id and isinstance(conv_id, str) and conv_id.startswith('user_'):
            try:
                # Formato: "user_{id}_session_{uuid}"
                parts = conv_id.split('_')
                if len(parts) >= 2:
                    user_id = int(parts[1])
                    return user_id
            except (IndexError, ValueError):
                pass

    return user_id


async def track_tool_start(
    input_data: HookInput,
    tool_use_id: Optional[str],
    context: HookContext
) -> HookJSONOutput:
    """
    Hook PreToolUse para registrar início de tool call (guarda no cache).

    O tracking real acontece no PostToolUse quando temos input + output.
    Também extrai e armazena o user_id para isolamento por usuário.
    """
    tool_name = input_data.get("tool_name", "unknown")
    tool_input = input_data.get("tool_input", {})

    # Extrair user_id para isolamento
    user_id = _extract_user_id(input_data, context)

    # Armazenar dados para uso no PostToolUse
    if tool_use_id:
        _tool_call_cache[tool_use_id] = {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "start_time": time.time(),
            "user_id": user_id  # Guardar para usar no PostToolUse
        }
        logger.debug(f"Tool tracking prepared: {tool_name} (user_id={user_id})")

    # Sempre continuar execução
    return {}


async def stop_on_critical_error(
    input_data: HookInput,
    tool_use_id: Optional[str],
    context: HookContext
) -> HookJSONOutput:
    """
    Hook PostToolUse para parar execução em erros críticos REAIS.

    IMPORTANTE: Só para em erros de EXECUÇÃO de ferramentas, nunca em leitura de arquivos.

    Ferramentas de leitura (Read, Glob, Grep, Bash) SEMPRE retornam conteúdo,
    então NÃO devem acionar este hook mesmo se o conteúdo contiver palavras como "error".

    Só para em:
    1. Ferramentas de ESCRITA/AÇÃO (execute_sql_query, save_diagnosis, etc)
    2. QUE retornaram is_error = True
    3. E contêm erros de sistema
    """
    tool_response = input_data.get("tool_response", "")
    tool_name = input_data.get("tool_name", "")
    is_error = input_data.get("is_error", False)

    # Lista de ferramentas que SEMPRE devem continuar (são ferramentas de leitura)
    read_only_tools = ["Read", "Glob", "Grep", "Bash", "List"]

    # Se é ferramenta de leitura, NUNCA parar (conteúdo pode ter palavras "error")
    if any(read_tool in tool_name for read_tool in read_only_tools):
        return {"continue_": True}

    # Se a ferramenta não marcou como erro, continuar
    if not is_error:
        return {"continue_": True}

    # Converter para string se necessário
    response_str = str(tool_response).lower()

    # Palavras-chave que indicam erro crítico DE SISTEMA (apenas para ferramentas de ação)
    critical_keywords = [
        "database connection failed",
        "authentication failed",
        "permission denied",
        "connection refused",
        "timeout error",
        "network error"
    ]

    for keyword in critical_keywords:
        if keyword in response_str:
            logger.error(f"Critical system error detected in {tool_name}: {keyword}")
            return {
                "continue_": False,
                "stopReason": f"Erro crítico de sistema: {keyword}",
                "systemMessage": f"🛑 Execução parada devido a erro crítico em {tool_name}"
            }

    # Continuar execução normal
    return {"continue_": True}


async def audit_tool_usage(
    input_data: HookInput,
    tool_use_id: Optional[str],
    context: HookContext
) -> HookJSONOutput:
    """
    Hook PostToolUse para auditar uso de ferramentas.

    Loga todas as ferramentas executadas E registra no AgentFS para analytics.

    ISOLAMENTO POR USUÁRIO:
    - Cada usuário tem seu próprio banco em .agentfs/user-{id}.db
    - O user_id é extraído do cache (PreToolUse) ou do contexto
    - Se não encontrar user_id, usa user_id=0 (sistema/global)
    """
    tool_name = input_data.get("tool_name", "unknown")
    tool_input = input_data.get("tool_input", {})
    tool_response = input_data.get("tool_response", "")
    is_error = input_data.get("is_error", False)

    # Buscar dados do cache (registrado no PreToolUse)
    cached = _tool_call_cache.pop(tool_use_id, None) if tool_use_id else None
    duration_ms = 0
    user_id = None

    if cached:
        duration_ms = int((time.time() - cached["start_time"]) * 1000)
        user_id = cached.get("user_id")

    # Fallback: tentar extrair user_id agora se não veio do cache
    if user_id is None:
        user_id = _extract_user_id(input_data, context)

    # Log de auditoria
    logger.info(
        f"Tool executed: {tool_name} | "
        f"tool_use_id: {tool_use_id} | "
        f"duration: {duration_ms}ms | "
        f"error: {is_error} | "
        f"user_id: {user_id}"
    )

    # Registrar no AgentFS Tool Tracking (isolado por usuário)
    try:
        from core.agentfs_client import get_agentfs

        # Obter AgentFS do usuário específico (ou global se user_id=None)
        agentfs = await get_agentfs(user_id=user_id)

        # Preparar output data
        output_data = {
            "success": not is_error,
            "duration_ms": duration_ms,
            "response_preview": str(tool_response)[:200] if tool_response else None
        }
        if is_error:
            output_data["error"] = str(tool_response)[:500]

        # Registrar chamada completa
        await agentfs.tool_track(tool_name, tool_input, output_data)
        logger.debug(f"Tool tracked in AgentFS for user {user_id}: {tool_name} ({duration_ms}ms)")

    except ImportError:
        # AgentFS não disponível
        pass
    except Exception as e:
        logger.warning(f"AgentFS tool tracking failed for user {user_id}: {e}")

    # Retornar vazio para não interferir na execução
    return {}


# ==============================================================================
# FACTORIES DE HOOKS COM USER_ID (para isolamento por usuário)
# ==============================================================================

def create_track_tool_start(user_id: int):
    """
    Factory que cria hook PreToolUse com user_id capturado via closure.

    Isso garante que o user_id esteja disponível para o hook independente
    do formato do conversation_id (que pode vir do frontend).

    Args:
        user_id: ID do usuário para isolamento no AgentFS

    Returns:
        Função hook que captura o user_id
    """
    async def _track_tool_start_with_user(
        input_data: HookInput,
        tool_use_id: Optional[str],
        context: HookContext
    ) -> HookJSONOutput:
        """Hook PreToolUse que registra início de tool call com user_id fixo"""
        tool_name = input_data.get("tool_name")
        tool_input = input_data.get("tool_input", {})

        if tool_use_id:
            _tool_call_cache[tool_use_id] = {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "start_time": time.time(),
                "user_id": user_id  # user_id capturado via closure
            }
            logger.debug(f"Tool tracking prepared: {tool_name} (user_id={user_id})")

        return {}

    return _track_tool_start_with_user


def create_audit_tool_usage(user_id: int):
    """
    Factory que cria hook PostToolUse com user_id capturado via closure.

    Args:
        user_id: ID do usuário para isolamento no AgentFS

    Returns:
        Função hook que audita tool usage no AgentFS do usuário
    """
    async def _audit_tool_usage_with_user(
        input_data: HookInput,
        tool_use_id: Optional[str],
        context: HookContext
    ) -> HookJSONOutput:
        """Hook PostToolUse que registra tool call completa no AgentFS do usuário"""
        try:
            from core.agentfs_client import get_agentfs
        except ImportError:
            return {}

        tool_name = input_data.get("tool_name")
        tool_input = input_data.get("tool_input", {})
        output = input_data.get("tool_output", {})

        # Obter dados do cache ou usar defaults
        cached = _tool_call_cache.pop(tool_use_id, None) if tool_use_id else None

        if cached:
            duration_ms = int((time.time() - cached["start_time"]) * 1000)
        else:
            duration_ms = 0

        # Preparar output data
        is_error = "error" in str(output).lower() if output else False
        output_data = {
            "result": output,
            "duration_ms": duration_ms,
        }
        if is_error:
            output_data["error"] = str(output)

        logger.debug(
            f"Auditing tool for user {user_id}: {tool_name} - "
            f"duration: {duration_ms}ms, error: {is_error}"
        )

        try:
            # Usar AgentFS do usuário específico (closure)
            agentfs = await get_agentfs(user_id=user_id)
            await agentfs.tool_track(tool_name, tool_input, output_data)
            logger.debug(f"Tool tracked in AgentFS for user {user_id}: {tool_name} ({duration_ms}ms)")
        except Exception as e:
            logger.warning(f"AgentFS tool tracking failed for user {user_id}: {e}")

        return {}

    return _audit_tool_usage_with_user
