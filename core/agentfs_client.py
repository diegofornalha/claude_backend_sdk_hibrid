"""
AgentFS Client - Camada de persistência para a Nanda (AI Agent)

ARQUITETURA HÍBRIDA:
- TURSO (crm.db): Dados do negócio (users, assessments, chat_sessions)
- AgentFS (.agentfs/user-{id}.db): Dados do agente por usuário (tool_calls, kv, arquivos)

Benefícios do isolamento por usuário:
- Auditoria isolada: sqlite3 user-123.db → vê tudo do usuário
- Portabilidade: pode exportar DB do usuário
- Sem vazamento: usuário não acessa dados de outro

Uso:
    # Obter AgentFS para um usuário específico
    agentfs = await get_agentfs(user_id=123)
    await agentfs.tool_track("minha_tool", {...}, {...})

    # Obter estatísticas do usuário
    stats = await agentfs.tool_stats()
"""

import os
import asyncio
import logging
from typing import Optional, List, Dict, Any, Union
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from agentfs_sdk import AgentFS, AgentFSOptions

load_dotenv()

logger = logging.getLogger(__name__)

# Configuração (mantida para compatibilidade, mas não usada no modo por usuário)
AGENTFS_AGENT_ID = os.getenv("AGENTFS_AGENT_ID", "crm")
AGENTFS_DATABASE_PATH = os.getenv("AGENTFS_DATABASE_PATH", "./crm-agent.db")
TURSO_SYNC_URL = os.getenv("TURSO_SYNC_URL", "")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")


class NandaAgentFS:
    """
    Cliente AgentFS para a Nanda.

    Fornece acesso a:
    - KV Store para sessões e configurações
    - Filesystem para arquivos gerados (gráficos, mapas)
    - Tool tracking para analytics

    Pode ser inicializado de duas formas:
    1. Com agent já existente (do AgentFSManager): NandaAgentFS(agent=agent, user_id=123)
    2. Standalone (compatibilidade): NandaAgentFS() + await initialize()
    """

    def __init__(
        self,
        agent: Optional[AgentFS] = None,
        user_id: Optional[int] = None
    ):
        """
        Inicializa o cliente.

        Args:
            agent: Instância AgentFS já inicializada (do manager)
            user_id: ID do usuário (para logging e identificação)
        """
        self._agent: Optional[AgentFS] = agent
        self._user_id = user_id
        self._agent_id = f"user-{user_id}" if user_id else AGENTFS_AGENT_ID
        self._db_path = AGENTFS_DATABASE_PATH
        self._sync_url = TURSO_SYNC_URL
        self._auth_token = TURSO_AUTH_TOKEN
        self._initialized = agent is not None

        if agent:
            logger.debug(f"NandaAgentFS wrapped existing agent for user {user_id}")
        else:
            logger.info(f"AgentFS Client initialized for agent: {self._agent_id}")
            logger.info(f"Database path: {self._db_path}")
            if self._sync_url:
                logger.info(f"Turso Sync enabled: {self._sync_url[:50]}...")

    async def initialize(self):
        """Inicializa conexão com AgentFS"""
        if self._initialized:
            return

        options = AgentFSOptions(
            id=self._agent_id,
            path=self._db_path,
        )

        # Configurar sync com Turso se disponível
        if self._sync_url and self._auth_token:
            options.sync_url = self._sync_url
            options.auth_token = self._auth_token
            logger.info("AgentFS configured with Turso Sync")
        else:
            logger.warning("AgentFS running in local-only mode (no Turso Sync)")

        self._agent = await AgentFS.open(options)
        self._initialized = True
        logger.info("AgentFS connection established")

    async def close(self):
        """Fecha conexão com AgentFS"""
        if self._agent:
            await self._agent.close()
            self._initialized = False
            logger.info("AgentFS connection closed")

    @property
    def agent(self) -> AgentFS:
        """Retorna instância do AgentFS"""
        if not self._initialized or not self._agent:
            raise RuntimeError(
                "AgentFS not initialized. Call await agent.initialize() first."
            )
        return self._agent

    # ==========================================================================
    # COMPATIBILIDADE COM CÓDIGO EXISTENTE (turso_database.py)
    # ==========================================================================

    async def query(
        self,
        sql: str,
        params: Union[tuple, list] = ()
    ) -> List[Dict[str, Any]]:
        """
        Compatibilidade com turso_database.query()

        NOTA: AgentFS não expõe SQL direto. Esta função é um wrapper
        para facilitar migração gradual. Use KV store quando possível.
        """
        logger.warning(
            f"Direct SQL query called: {sql[:50]}... "
            "Consider migrating to KV store or filesystem API"
        )

        # TODO: Implementar fallback ou migrar queries para KV store
        raise NotImplementedError(
            "Direct SQL queries not supported in AgentFS. "
            "Use agent.kv for data storage or migrate to structured API."
        )

    async def execute(
        self,
        sql: str,
        params: Union[tuple, list] = ()
    ) -> int:
        """
        Compatibilidade com turso_database.execute()

        NOTA: AgentFS não expõe SQL direto. Use KV store ou filesystem.
        """
        logger.warning(
            f"Direct SQL execute called: {sql[:50]}... "
            "Consider migrating to KV store or filesystem API"
        )

        raise NotImplementedError(
            "Direct SQL execution not supported in AgentFS. "
            "Use agent.kv.set() or agent.fs.write_file() instead."
        )

    # ==========================================================================
    # KV STORE - Key-Value Storage
    # ==========================================================================

    async def kv_set(self, key: str, value: Any) -> None:
        """Salva valor no KV store"""
        await self.agent.kv.set(key, value)
        logger.debug(f"KV set: {key}")

    async def kv_get(self, key: str) -> Optional[Any]:
        """Obtém valor do KV store"""
        value = await self.agent.kv.get(key)
        logger.debug(f"KV get: {key} -> {'found' if value else 'not found'}")
        return value

    async def kv_delete(self, key: str) -> None:
        """Remove valor do KV store"""
        await self.agent.kv.delete(key)
        logger.debug(f"KV delete: {key}")

    async def kv_list(self, prefix: str) -> List[str]:
        """
        Lista chaves com prefixo usando índice manual.

        NOTA: O AgentFS SDK não tem método list() no KVStore,
        então mantemos um índice em `_kv_index:{prefix_base}`.
        """
        # Extrair base do prefix (ex: "tool_call:" -> "tool_call")
        prefix_base = prefix.rstrip(":")
        index_key = f"_kv_index:{prefix_base}"

        try:
            index = await self.agent.kv.get(index_key)
            if index and isinstance(index, list):
                # Filtrar apenas chaves que ainda começam com o prefix
                keys = [k for k in index if k.startswith(prefix_base)]
                logger.debug(f"KV list: {prefix}* -> {len(keys)} keys")
                return keys
        except Exception as e:
            logger.debug(f"KV index not found or error: {e}")

        return []

    async def _kv_index_add(self, key: str, prefix_base: str) -> None:
        """Adiciona uma chave ao índice de um prefixo"""
        index_key = f"_kv_index:{prefix_base}"
        try:
            index = await self.agent.kv.get(index_key)
            if not index or not isinstance(index, list):
                index = []
            if key not in index:
                index.append(key)
                await self.agent.kv.set(index_key, index)
        except Exception as e:
            logger.warning(f"Failed to update KV index: {e}")

    # ==========================================================================
    # FILESYSTEM - Virtual Filesystem
    # ==========================================================================

    async def fs_write(self, path: str, content: Union[str, bytes]) -> None:
        """Escreve arquivo"""
        await self.agent.fs.write_file(path, content)
        logger.debug(f"FS write: {path}")

    async def fs_read(self, path: str, as_bytes: bool = False) -> Union[str, bytes]:
        """Lê arquivo"""
        encoding = None if as_bytes else 'utf-8'
        content = await self.agent.fs.read_file(path, encoding=encoding)
        logger.debug(f"FS read: {path}")
        return content

    async def fs_delete(self, path: str) -> None:
        """Remove arquivo"""
        await self.agent.fs.delete_file(path)
        logger.debug(f"FS delete: {path}")

    async def fs_list(self, path: str) -> List[Dict[str, Any]]:
        """Lista arquivos em diretório"""
        entries = await self.agent.fs.readdir(path)
        logger.debug(f"FS list: {path} -> {len(entries)} entries")
        return entries

    # ==========================================================================
    # TOOL TRACKING - Analytics de ferramentas MCP (API nativa do AgentFS)
    # ==========================================================================

    async def tool_track(
        self,
        name: str,
        input_data: Dict = None,
        output_data: Any = None
    ) -> None:
        """
        Registra uma chamada de ferramenta completa na tabela nativa tool_calls.

        Usa agent.tools.record() para INSERT direto na tabela tool_calls.
        A tabela é INSERT-ONLY (imutável) - perfeita para auditoria.

        API: record(name, started_at, completed_at, parameters=None, result=None, error=None)

        Args:
            name: Nome da ferramenta
            input_data: Parâmetros de entrada
            output_data: Resultado/resposta (deve conter duration_ms e opcionalmente error)
        """
        import json
        import time

        try:
            duration_ms = output_data.get("duration_ms", 0) if output_data else 0
            is_error = output_data.get("error") if output_data else None

            # Calcular timestamps (SDK usa segundos, não ms)
            completed_at = int(time.time())
            started_at = completed_at - (duration_ms // 1000) if duration_ms > 1000 else completed_at

            # Serializar para JSON (a tabela espera TEXT)
            params_json = json.dumps(input_data) if input_data else None
            result_json = json.dumps(output_data) if output_data else None

            if is_error:
                # Registrar erro
                await self.agent.tools.record(
                    name=name,
                    started_at=started_at,
                    completed_at=completed_at,
                    parameters=params_json,
                    error=str(is_error)
                )
                logger.info(f"Tool tracked (ERROR): {name} ({duration_ms}ms)")
            else:
                # Registrar sucesso
                await self.agent.tools.record(
                    name=name,
                    started_at=started_at,
                    completed_at=completed_at,
                    parameters=params_json,
                    result=result_json
                )
                logger.info(f"Tool tracked (OK): {name} ({duration_ms}ms)")

        except Exception as e:
            logger.warning(f"Tool track failed: {e}")
            # Fallback para KV se o método nativo falhar
            await self._tool_track_kv_fallback(name, input_data, output_data)

    async def _tool_track_kv_fallback(
        self,
        name: str,
        input_data: Dict = None,
        output_data: Any = None
    ) -> None:
        """Fallback para KV se o tracking nativo falhar"""
        import time
        try:
            started_at = int(time.time())
            duration_ms = output_data.get("duration_ms", 0) if output_data else 0
            is_error = output_data.get("error") if output_data else None
            status = "error" if is_error else "success"

            call_key = f"tool_call:{name}:{started_at}"
            call_data = {
                "name": name,
                "parameters": input_data,
                "result": output_data,
                "status": status,
                "started_at": started_at,
                "duration_ms": duration_ms
            }
            await self.kv_set(call_key, call_data)
            await self._kv_index_add(call_key, "tool_call")
            logger.info(f"Tool tracked via KV fallback: {name}")
        except Exception as e:
            logger.error(f"KV fallback also failed: {e}")

    async def tool_stats(self) -> List[Any]:
        """
        Obtém estatísticas de ferramentas da tabela nativa tool_calls.

        Usa agent.tools.get_stats() para agregar dados.
        """
        try:
            # Usar API nativa do AgentFS
            stats = await self.agent.tools.get_stats()
            logger.debug(f"Tool stats (native): {len(stats) if stats else 0} tools")
            return stats if stats else []

        except Exception as e:
            logger.warning(f"Native tool stats failed: {e}, trying KV fallback")
            return await self._tool_stats_kv_fallback()

    async def _tool_stats_kv_fallback(self) -> List[Any]:
        """Fallback para KV se stats nativo falhar"""
        try:
            keys = await self.kv_list("tool_call:")
            if not keys:
                return []

            stats_by_tool = {}
            for key in keys:
                try:
                    data = await self.kv_get(key)
                    if data:
                        tool_name = data.get("name", "unknown")
                        if tool_name not in stats_by_tool:
                            stats_by_tool[tool_name] = {
                                "name": tool_name,
                                "calls": 0,
                                "successes": 0,
                                "errors": 0,
                                "total_duration_ms": 0
                            }
                        stats_by_tool[tool_name]["calls"] += 1
                        if data.get("status") == "success":
                            stats_by_tool[tool_name]["successes"] += 1
                        else:
                            stats_by_tool[tool_name]["errors"] += 1
                        stats_by_tool[tool_name]["total_duration_ms"] += data.get("duration_ms", 0)
                except Exception:
                    pass

            return list(stats_by_tool.values())
        except Exception as e:
            logger.warning(f"KV fallback stats failed: {e}")
            return []

    async def tool_get_recent(self, limit: int = 10, hours: int = 24) -> List[Dict]:
        """
        Obtém as chamadas mais recentes da tabela tool_calls.

        API: get_recent(since, limit=None)

        Args:
            limit: Número máximo de resultados
            hours: Buscar chamadas das últimas N horas (default: 24)

        Returns:
            Lista de tool calls ordenadas por started_at DESC
        """
        import time
        try:
            # Calcular timestamp de "since" (N horas atrás)
            since = int(time.time()) - (hours * 3600)
            calls = await self.agent.tools.get_recent(since=since, limit=limit)
            return calls if calls else []
        except Exception as e:
            logger.warning(f"Get recent tools failed: {e}")
            return []

    async def tool_get_by_name(self, name: str, limit: int = 10) -> List[Dict]:
        """
        Obtém chamadas de uma ferramenta específica.

        Args:
            name: Nome da ferramenta
            limit: Número máximo de resultados

        Returns:
            Lista de tool calls filtradas por nome
        """
        try:
            calls = await self.agent.tools.get_by_name(name=name, limit=limit)
            return calls if calls else []
        except Exception as e:
            logger.warning(f"Get tools by name failed: {e}")
            return []

    # ==========================================================================
    # CLEANUP/RETENTION - Limpeza de dados antigos
    # ==========================================================================

    async def cleanup_old_tool_calls(self, days: int = 30) -> int:
        """
        Remove tool_calls mais antigos que N dias.

        Args:
            days: Número de dias de retenção (default: 30)

        Returns:
            Número de registros deletados
        """
        import time
        try:
            db = self.agent.get_database()
            cutoff = int(time.time()) - (days * 86400)

            cursor = await db.execute(
                "DELETE FROM tool_calls WHERE started_at < ?",
                [cutoff]
            )
            deleted = cursor.rowcount
            await cursor.close()

            if deleted > 0:
                logger.info(f"Cleanup: removed {deleted} tool_calls > {days} days (user {self._user_id})")
            return deleted

        except Exception as e:
            logger.warning(f"Cleanup tool_calls failed for user {self._user_id}: {e}")
            return 0

    async def cleanup_old_kv_entries(self, days: int = 90) -> int:
        """
        Remove KV entries mais antigos que N dias.

        Args:
            days: Número de dias de retenção (default: 90)

        Returns:
            Número de registros deletados
        """
        import time
        try:
            db = self.agent.get_database()
            cutoff = int(time.time()) - (days * 86400)

            cursor = await db.execute(
                "DELETE FROM kv_store WHERE updated_at < ?",
                [cutoff]
            )
            deleted = cursor.rowcount
            await cursor.close()

            if deleted > 0:
                logger.info(f"Cleanup: removed {deleted} kv entries > {days} days (user {self._user_id})")
            return deleted

        except Exception as e:
            logger.warning(f"Cleanup kv_store failed for user {self._user_id}: {e}")
            return 0

    async def get_db_size_mb(self) -> float:
        """
        Retorna tamanho do banco do usuário em MB.

        Returns:
            Tamanho em megabytes
        """
        try:
            db_path = f".agentfs/user-{self._user_id}.db"
            if os.path.exists(db_path):
                size_bytes = os.path.getsize(db_path)
                # Incluir arquivos WAL e SHM se existirem
                for ext in ["-wal", "-shm"]:
                    aux_path = db_path + ext
                    if os.path.exists(aux_path):
                        size_bytes += os.path.getsize(aux_path)
                return size_bytes / (1024 * 1024)
            return 0.0
        except Exception as e:
            logger.warning(f"Get DB size failed for user {self._user_id}: {e}")
            return 0.0

    async def vacuum_database(self) -> bool:
        """
        Compacta o banco após cleanup (recupera espaço em disco).

        Returns:
            True se sucesso, False se falhou
        """
        try:
            db = self.agent.get_database()
            await db.execute("VACUUM")
            logger.info(f"VACUUM completed for user {self._user_id}")
            return True
        except Exception as e:
            logger.warning(f"VACUUM failed for user {self._user_id}: {e}")
            return False

    async def get_retention_stats(self) -> Dict[str, Any]:
        """
        Obtém estatísticas para decisão de cleanup.

        Returns:
            Dict com contagens e tamanhos
        """
        import time
        try:
            db = self.agent.get_database()
            now = int(time.time())

            # Contar tool_calls por idade
            cursor = await db.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN started_at < ? THEN 1 ELSE 0 END) as older_30d,
                    SUM(CASE WHEN started_at < ? THEN 1 ELSE 0 END) as older_7d
                FROM tool_calls
            """, [now - (30 * 86400), now - (7 * 86400)])
            tc_row = await cursor.fetchone()
            await cursor.close()

            # Contar kv_store por idade
            cursor = await db.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN updated_at < ? THEN 1 ELSE 0 END) as older_90d,
                    SUM(CASE WHEN updated_at < ? THEN 1 ELSE 0 END) as older_30d
                FROM kv_store
            """, [now - (90 * 86400), now - (30 * 86400)])
            kv_row = await cursor.fetchone()
            await cursor.close()

            return {
                "user_id": self._user_id,
                "db_size_mb": await self.get_db_size_mb(),
                "tool_calls": {
                    "total": tc_row[0] if tc_row else 0,
                    "older_30d": tc_row[1] if tc_row else 0,
                    "older_7d": tc_row[2] if tc_row else 0,
                },
                "kv_store": {
                    "total": kv_row[0] if kv_row else 0,
                    "older_90d": kv_row[1] if kv_row else 0,
                    "older_30d": kv_row[2] if kv_row else 0,
                }
            }
        except Exception as e:
            logger.warning(f"Get retention stats failed for user {self._user_id}: {e}")
            return {"user_id": self._user_id, "error": str(e)}


# ==============================================================================
# Instância global e gerenciamento de lifecycle
# ==============================================================================

# Cache de instâncias NandaAgentFS por user_id
_user_clients: Dict[int, NandaAgentFS] = {}


async def get_agentfs(user_id: Optional[int] = None) -> NandaAgentFS:
    """
    Obtém instância AgentFS para um usuário específico.

    ARQUITETURA:
    - Cada usuário tem seu próprio banco em .agentfs/user-{id}.db
    - Isso garante isolamento total de dados do agente por usuário

    Args:
        user_id: ID do usuário. Se None, usa user_id=0 (global/sistema)

    Returns:
        NandaAgentFS: Cliente wrapper com métodos úteis

    Exemplo:
        agentfs = await get_agentfs(user_id=123)
        await agentfs.tool_track("save_diagnosis", {...}, {...})
    """
    from core.agentfs_manager import get_agentfs_for_user

    effective_user_id = user_id if user_id is not None else 0

    # Verificar cache local de wrappers
    if effective_user_id in _user_clients:
        return _user_clients[effective_user_id]

    # Obter agent do manager e criar wrapper
    agent = await get_agentfs_for_user(effective_user_id)
    wrapper = NandaAgentFS(agent=agent, user_id=effective_user_id)
    _user_clients[effective_user_id] = wrapper

    return wrapper


async def close_agentfs():
    """Fecha todas as conexões AgentFS"""
    from core.agentfs_manager import close_all_agentfs

    global _user_clients
    _user_clients.clear()
    await close_all_agentfs()


@asynccontextmanager
async def agentfs_context(user_id: Optional[int] = None):
    """
    Context manager para uso do AgentFS.

    Args:
        user_id: ID do usuário (opcional)

    Exemplo:
        async with agentfs_context(user_id=123) as agentfs:
            await agentfs.kv_set("key", "value")
    """
    client = await get_agentfs(user_id=user_id)
    try:
        yield client
    finally:
        pass  # Não fechar aqui, manager gerencia lifecycle


# ==============================================================================
# Compatibilidade com código existente
# ==============================================================================

def get_db_connection():
    """
    DEPRECATED: Use get_agentfs(user_id=X) ao invés.

    Compatibilidade com turso_database.get_db_connection()

    AVISO: Esta função retorna None. Migre para usar as APIs do AgentFS.
    """
    logger.warning(
        "get_db_connection() is DEPRECATED. "
        "Use 'await get_agentfs(user_id=X)' instead."
    )
    return None


# Aliases para compatibilidade
agentfs = get_db_connection  # alias (deprecated)
