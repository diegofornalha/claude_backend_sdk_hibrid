"""
AgentFS Manager - Pool de instâncias AgentFS por usuário

Cada usuário tem seu próprio banco de dados em .agentfs/user-{id}.db

Arquitetura:
- TURSO (crm.db): Dados do negócio (users, assessments, chat_sessions)
- AgentFS (.agentfs/user-{id}.db): Dados do agente (tool_calls, kv_store, arquivos)

Benefícios:
- Auditoria isolada por usuário
- Portabilidade (pode exportar DB do usuário)
- Sem vazamento de dados entre usuários
"""

import asyncio
import logging
import time
import os
from typing import Dict, Optional, List
from pathlib import Path

from agentfs_sdk import AgentFS, AgentFSOptions

logger = logging.getLogger(__name__)

# Configuração
AGENTFS_BASE_PATH = "./.agentfs"
AGENTFS_IDLE_TIMEOUT = int(os.getenv("AGENTFS_IDLE_TIMEOUT", "300"))  # 5 min default


class AgentFSManager:
    """
    Gerencia múltiplas instâncias de AgentFS (1 por usuário).

    Implementa lazy initialization - só abre conexão quando usuário precisa.
    Mantém pool de conexões ativas para performance.
    Fecha conexões inativas após AGENTFS_IDLE_TIMEOUT segundos.
    """

    def __init__(self, base_path: str = AGENTFS_BASE_PATH, idle_timeout: int = AGENTFS_IDLE_TIMEOUT):
        self._instances: Dict[int, AgentFS] = {}
        self._last_access: Dict[int, float] = {}  # Timestamp de último acesso por usuário
        self._idle_timeout = idle_timeout  # Segundos de inatividade antes de fechar
        self._cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._base_path = Path(base_path)
        self._base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"AgentFSManager initialized at {self._base_path} (idle_timeout={idle_timeout}s)")

    async def get_for_user(self, user_id: int) -> AgentFS:
        """
        Obtém (ou cria) instância AgentFS para um usuário.

        Args:
            user_id: ID do usuário

        Returns:
            Instância AgentFS conectada ao banco do usuário
        """
        async with self._lock:
            if user_id not in self._instances:
                db_path = self._base_path / f"user-{user_id}.db"
                options = AgentFSOptions(
                    id=f"user-{user_id}",
                    path=str(db_path)
                )
                agent = await AgentFS.open(options)
                self._instances[user_id] = agent
                logger.info(f"AgentFS opened for user {user_id}: {db_path}")

            # Atualizar timestamp de último acesso
            self._last_access[user_id] = time.time()
            return self._instances[user_id]

    async def close_for_user(self, user_id: int) -> None:
        """
        Fecha conexão de um usuário específico.

        Args:
            user_id: ID do usuário
        """
        async with self._lock:
            if user_id in self._instances:
                await self._instances[user_id].close()
                del self._instances[user_id]
                self._last_access.pop(user_id, None)  # Limpar tracking
                logger.info(f"AgentFS closed for user {user_id}")

    async def close_all(self) -> None:
        """Fecha todas as conexões ativas."""
        async with self._lock:
            for user_id, agent in list(self._instances.items()):
                try:
                    await agent.close()
                    logger.info(f"AgentFS closed for user {user_id}")
                except Exception as e:
                    logger.warning(f"Error closing AgentFS for user {user_id}: {e}")
            self._instances.clear()
            self._last_access.clear()  # Limpar tracking
            logger.info("All AgentFS connections closed")

    # =========================================================================
    # IDLE CONNECTION CLEANUP
    # =========================================================================

    async def _cleanup_idle_connections(self) -> None:
        """
        Loop que verifica e fecha conexões inativas.
        Roda a cada 60 segundos.
        """
        while True:
            try:
                await asyncio.sleep(60)  # Verificar a cada minuto

                now = time.time()
                idle_users = []

                async with self._lock:
                    # Identificar usuários inativos
                    for uid, last_access in list(self._last_access.items()):
                        idle_seconds = now - last_access
                        if idle_seconds > self._idle_timeout:
                            idle_users.append((uid, idle_seconds))

                # Fechar conexões inativas (fora do lock para evitar deadlock)
                for uid, idle_seconds in idle_users:
                    try:
                        async with self._lock:
                            if uid in self._instances:
                                await self._instances[uid].close()
                                del self._instances[uid]
                                del self._last_access[uid]
                                logger.info(f"Closed idle AgentFS for user {uid} ({int(idle_seconds)}s idle)")
                    except Exception as e:
                        logger.warning(f"Error closing idle AgentFS for user {uid}: {e}")

            except asyncio.CancelledError:
                logger.info("AgentFS idle cleanup task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in AgentFS idle cleanup: {e}")

    def start_cleanup_task(self) -> None:
        """Inicia tarefa de cleanup de conexões inativas."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_idle_connections())
            logger.info(f"AgentFS idle cleanup started (timeout={self._idle_timeout}s)")

    def stop_cleanup_task(self) -> None:
        """Para tarefa de cleanup."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
            logger.info("AgentFS idle cleanup stopped")

    def list_user_dbs(self) -> List[int]:
        """
        Lista IDs de usuários com bancos de dados existentes.

        Returns:
            Lista de user_ids ordenados
        """
        user_ids = []
        for db_file in self._base_path.glob("user-*.db"):
            # Ignorar arquivos WAL e SHM
            if db_file.suffix in ['.db-wal', '.db-shm']:
                continue
            try:
                user_id = int(db_file.stem.replace("user-", ""))
                user_ids.append(user_id)
            except ValueError:
                logger.warning(f"Invalid AgentFS db filename: {db_file}")
        return sorted(user_ids)

    def get_active_users(self) -> List[int]:
        """
        Lista IDs de usuários com conexões ativas.

        Returns:
            Lista de user_ids com conexões abertas
        """
        return list(self._instances.keys())

    def get_db_path(self, user_id: int) -> Path:
        """
        Retorna caminho do banco de dados de um usuário.

        Args:
            user_id: ID do usuário

        Returns:
            Path do arquivo .db
        """
        return self._base_path / f"user-{user_id}.db"

    def db_exists(self, user_id: int) -> bool:
        """
        Verifica se o banco de dados de um usuário existe.

        Args:
            user_id: ID do usuário

        Returns:
            True se o arquivo .db existe
        """
        return self.get_db_path(user_id).exists()

    # ==========================================================================
    # CLEANUP/RETENTION - Limpeza de dados antigos em batch
    # ==========================================================================

    async def cleanup_all_users(
        self,
        tool_calls_days: int = 30,
        kv_days: int = 90,
        vacuum: bool = True
    ) -> dict:
        """
        Executa cleanup em todos os bancos de usuários.

        Política de retenção:
        - tool_calls: remove registros > tool_calls_days
        - kv_store: remove registros > kv_days
        - vacuum: compacta DB após limpeza (recupera espaço)

        Args:
            tool_calls_days: Dias de retenção para tool_calls (default: 30)
            kv_days: Dias de retenção para kv_store (default: 90)
            vacuum: Executar VACUUM após cleanup (default: True)

        Returns:
            Dict com estatísticas do cleanup
        """
        from core.agentfs_client import NandaAgentFS

        results = {
            "users_processed": 0,
            "users_cleaned": 0,
            "tool_calls_deleted": 0,
            "kv_deleted": 0,
            "errors": []
        }

        user_ids = self.list_user_dbs()
        logger.info(f"[AgentFS Cleanup] Starting cleanup for {len(user_ids)} users")

        for user_id in user_ids:
            try:
                # Obter ou criar conexão
                agent = await self.get_for_user(user_id)
                wrapper = NandaAgentFS(agent=agent, user_id=user_id)

                results["users_processed"] += 1

                # Cleanup tool_calls
                tc_deleted = await wrapper.cleanup_old_tool_calls(tool_calls_days)

                # Cleanup kv_store
                kv_deleted = await wrapper.cleanup_old_kv_entries(kv_days)

                # Contabilizar
                if tc_deleted > 0 or kv_deleted > 0:
                    results["users_cleaned"] += 1
                    results["tool_calls_deleted"] += tc_deleted
                    results["kv_deleted"] += kv_deleted

                    # VACUUM para recuperar espaço
                    if vacuum:
                        await wrapper.vacuum_database()

                    logger.info(
                        f"[AgentFS Cleanup] User {user_id}: "
                        f"{tc_deleted} tool_calls, {kv_deleted} kv entries"
                    )

            except Exception as e:
                error_msg = f"User {user_id}: {str(e)}"
                results["errors"].append(error_msg)
                logger.warning(f"[AgentFS Cleanup] Failed for user {user_id}: {e}")

        logger.info(
            f"[AgentFS Cleanup] Complete: "
            f"{results['users_cleaned']}/{results['users_processed']} users cleaned, "
            f"{results['tool_calls_deleted']} tool_calls removed, "
            f"{results['kv_deleted']} kv entries removed"
        )

        return results

    async def get_all_retention_stats(self) -> List[dict]:
        """
        Obtém estatísticas de retenção de todos os usuários.

        Útil para monitoramento e dashboards.

        Returns:
            Lista de stats por usuário
        """
        from core.agentfs_client import NandaAgentFS

        all_stats = []
        user_ids = self.list_user_dbs()

        for user_id in user_ids:
            try:
                agent = await self.get_for_user(user_id)
                wrapper = NandaAgentFS(agent=agent, user_id=user_id)
                stats = await wrapper.get_retention_stats()
                all_stats.append(stats)
            except Exception as e:
                all_stats.append({
                    "user_id": user_id,
                    "error": str(e)
                })

        return all_stats

    def get_total_storage_mb(self) -> float:
        """
        Calcula espaço total usado por todos os bancos AgentFS.

        Returns:
            Tamanho total em MB
        """
        import os
        total_bytes = 0
        for db_file in self._base_path.glob("user-*.db*"):
            try:
                total_bytes += os.path.getsize(db_file)
            except Exception:
                pass
        return total_bytes / (1024 * 1024)

    # ==========================================================================
    # SELF-AWARENESS - Agregação de dados para monitoramento
    # ==========================================================================

    async def get_system_health_data(self, hours: int = 24) -> dict:
        """
        Agrega dados de saúde de todos os usuários.

        Args:
            hours: Período de análise em horas (default: 24)

        Returns:
            Dict com métricas agregadas do sistema
        """
        import time
        from core.agentfs_client import NandaAgentFS

        user_ids = self.list_user_dbs()
        since = int(time.time()) - (hours * 3600)

        total_calls = 0
        total_success = 0
        total_errors = 0
        total_duration_ms = 0
        active_users = 0

        for user_id in user_ids:
            try:
                agent = await self.get_for_user(user_id)
                wrapper = NandaAgentFS(agent=agent, user_id=user_id)

                # Buscar calls recentes
                recent = await wrapper.tool_get_recent(limit=1000, hours=hours)
                if recent:
                    active_users += 1
                    for call in recent:
                        total_calls += 1
                        status = getattr(call, 'status', 'unknown')
                        if status == 'success' or status == 'completed':
                            total_success += 1
                        elif status == 'error' or status == 'failed':
                            total_errors += 1
                        duration = getattr(call, 'duration_ms', 0) or 0
                        total_duration_ms += duration

            except Exception as e:
                logger.warning(f"Health check failed for user {user_id}: {e}")

        success_rate = (total_success / total_calls) if total_calls > 0 else 1.0
        avg_duration_ms = (total_duration_ms / total_calls) if total_calls > 0 else 0

        return {
            "total_users": len(user_ids),
            "active_users": active_users,
            "total_storage_mb": self.get_total_storage_mb(),
            "period_hours": hours,
            "total_calls": total_calls,
            "success_calls": total_success,
            "error_calls": total_errors,
            "success_rate": success_rate,
            "avg_duration_ms": avg_duration_ms
        }

    async def get_problematic_tools(
        self,
        hours: int = 24,
        error_threshold: float = 0.1,
        slow_threshold_ms: int = 5000
    ) -> List[dict]:
        """
        Identifica ferramentas com problemas (alta taxa de erro ou lentidão).

        Args:
            hours: Período de análise em horas (default: 24)
            error_threshold: Taxa de erro para alertar (default: 10%)
            slow_threshold_ms: Tempo em ms para considerar lento (default: 5000)

        Returns:
            Lista de ferramentas problemáticas com detalhes
        """
        import time
        from core.agentfs_client import NandaAgentFS

        user_ids = self.list_user_dbs()

        # Agregar stats por ferramenta
        tool_stats: Dict[str, dict] = {}

        for user_id in user_ids:
            try:
                agent = await self.get_for_user(user_id)
                wrapper = NandaAgentFS(agent=agent, user_id=user_id)

                recent = await wrapper.tool_get_recent(limit=1000, hours=hours)
                for call in recent:
                    name = getattr(call, 'name', 'unknown')
                    status = getattr(call, 'status', 'unknown')
                    duration = getattr(call, 'duration_ms', 0) or 0

                    if name not in tool_stats:
                        tool_stats[name] = {
                            "total": 0,
                            "success": 0,
                            "errors": 0,
                            "total_duration_ms": 0
                        }

                    tool_stats[name]["total"] += 1
                    if status == 'success' or status == 'completed':
                        tool_stats[name]["success"] += 1
                    elif status == 'error' or status == 'failed':
                        tool_stats[name]["errors"] += 1
                    tool_stats[name]["total_duration_ms"] += duration

            except Exception as e:
                logger.warning(f"Tool stats failed for user {user_id}: {e}")

        # Identificar problemas
        problems = []
        for name, stats in tool_stats.items():
            if stats["total"] == 0:
                continue

            error_rate = stats["errors"] / stats["total"]
            avg_duration = stats["total_duration_ms"] / stats["total"]

            issues = []
            if error_rate >= error_threshold:
                issues.append(f"{error_rate:.0%} de erro ({stats['errors']}/{stats['total']})")

            if avg_duration >= slow_threshold_ms:
                issues.append(f"lento ({avg_duration:.0f}ms média)")

            if issues:
                problems.append({
                    "tool": name,
                    "total_calls": stats["total"],
                    "error_rate": error_rate,
                    "avg_duration_ms": avg_duration,
                    "issues": issues,
                    "issue": " | ".join(issues)
                })

        # Ordenar por severidade (error_rate primeiro)
        problems.sort(key=lambda x: (-x["error_rate"], -x["avg_duration_ms"]))

        return problems

    async def get_user_activity_data(
        self,
        hours: int = 24,
        top_n: int = 10
    ) -> List[dict]:
        """
        Ranking de atividade por usuário.

        Args:
            hours: Período de análise em horas (default: 24)
            top_n: Top N usuários mais ativos (default: 10)

        Returns:
            Lista de usuários ordenados por atividade
        """
        import time
        from core.agentfs_client import NandaAgentFS

        user_ids = self.list_user_dbs()
        user_activity = []

        for user_id in user_ids:
            try:
                agent = await self.get_for_user(user_id)
                wrapper = NandaAgentFS(agent=agent, user_id=user_id)

                recent = await wrapper.tool_get_recent(limit=1000, hours=hours)

                if recent:
                    total_calls = len(recent)
                    success = sum(1 for c in recent if getattr(c, 'status', '') in ['success', 'completed'])
                    errors = sum(1 for c in recent if getattr(c, 'status', '') in ['error', 'failed'])

                    # Última atividade
                    last_call = max(recent, key=lambda c: getattr(c, 'started_at', 0))
                    last_activity = getattr(last_call, 'started_at', 0)

                    # Tools mais usadas
                    tool_counts: Dict[str, int] = {}
                    for call in recent:
                        name = getattr(call, 'name', 'unknown')
                        tool_counts[name] = tool_counts.get(name, 0) + 1
                    top_tools = sorted(tool_counts.items(), key=lambda x: -x[1])[:3]

                    user_activity.append({
                        "user_id": user_id,
                        "total_calls": total_calls,
                        "success": success,
                        "errors": errors,
                        "success_rate": success / total_calls if total_calls > 0 else 0,
                        "last_activity": last_activity,
                        "top_tools": [{"name": t[0], "count": t[1]} for t in top_tools],
                        "db_size_mb": await wrapper.get_db_size_mb()
                    })

            except Exception as e:
                logger.warning(f"Activity check failed for user {user_id}: {e}")

        # Ordenar por atividade (mais ativo primeiro)
        user_activity.sort(key=lambda x: -x["total_calls"])

        return user_activity[:top_n]

    async def get_storage_report(self) -> dict:
        """
        Relatório detalhado de uso de storage.

        Returns:
            Dict com informações de storage por usuário
        """
        import os
        from core.agentfs_client import NandaAgentFS

        user_ids = self.list_user_dbs()
        users_storage = []
        total_size = 0

        for user_id in user_ids:
            try:
                db_path = self.get_db_path(user_id)
                size_bytes = os.path.getsize(db_path) if db_path.exists() else 0

                # Incluir WAL e SHM
                for ext in ["-wal", "-shm"]:
                    aux_path = str(db_path) + ext
                    if os.path.exists(aux_path):
                        size_bytes += os.path.getsize(aux_path)

                size_mb = size_bytes / (1024 * 1024)
                total_size += size_mb

                # Verificar se precisa de cleanup
                agent = await self.get_for_user(user_id)
                wrapper = NandaAgentFS(agent=agent, user_id=user_id)
                stats = await wrapper.get_retention_stats()

                needs_cleanup = (
                    stats.get("tool_calls", {}).get("older_30d", 0) or 0 > 0 or
                    stats.get("kv_store", {}).get("older_90d", 0) or 0 > 0
                )

                users_storage.append({
                    "user_id": user_id,
                    "size_mb": size_mb,
                    "tool_calls_total": stats.get("tool_calls", {}).get("total", 0),
                    "kv_total": stats.get("kv_store", {}).get("total", 0),
                    "needs_cleanup": needs_cleanup
                })

            except Exception as e:
                logger.warning(f"Storage check failed for user {user_id}: {e}")

        # Ordenar por tamanho (maior primeiro)
        users_storage.sort(key=lambda x: -x["size_mb"])

        return {
            "total_storage_mb": total_size,
            "total_users": len(user_ids),
            "users": users_storage,
            "candidates_cleanup": [u for u in users_storage if u.get("needs_cleanup")]
        }


# ==============================================================================
# Instância global e funções de conveniência
# ==============================================================================

_manager: Optional[AgentFSManager] = None


async def get_agentfs_manager() -> AgentFSManager:
    """
    Obtém o manager global.

    Returns:
        Instância singleton do AgentFSManager
    """
    global _manager
    if _manager is None:
        _manager = AgentFSManager()
    return _manager


async def get_agentfs_for_user(user_id: int) -> AgentFS:
    """
    Atalho: obtém AgentFS para um usuário específico.

    Args:
        user_id: ID do usuário

    Returns:
        Instância AgentFS do usuário
    """
    manager = await get_agentfs_manager()
    return await manager.get_for_user(user_id)


async def close_all_agentfs() -> None:
    """Fecha todas as conexões AgentFS."""
    global _manager
    if _manager:
        await _manager.close_all()
        _manager = None


def reset_manager() -> None:
    """
    Reseta o manager global (para testes).

    AVISO: Não fecha conexões existentes!
    """
    global _manager
    _manager = None
