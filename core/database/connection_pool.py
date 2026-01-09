"""
Connection Pool para gerenciamento eficiente de conexoes ao banco de dados.

Features:
- Pool de conexoes reutilizaveis
- Limite maximo de conexoes
- Timeout para obter conexao
- Limpeza automatica de conexoes ociosas
- Thread-safe
"""

import sqlite3
import threading
import time
import logging
from typing import Optional
from contextlib import contextmanager
from queue import Queue, Empty, Full
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PoolConfig:
    """Configuracao do pool de conexoes."""
    min_size: int = 2
    max_size: int = 10
    timeout: float = 5.0
    max_idle_time: float = 300.0  # 5 minutos
    health_check_interval: float = 30.0


class PooledConnection:
    """Wrapper para conexao com metadados do pool."""

    def __init__(self, connection: sqlite3.Connection, created_at: float):
        self.connection = connection
        self.created_at = created_at
        self.last_used = created_at
        self.in_use = False

    def is_healthy(self) -> bool:
        """Verifica se a conexao ainda esta saudavel."""
        try:
            self.connection.execute("SELECT 1")
            return True
        except Exception:
            return False

    def is_idle_too_long(self, max_idle_time: float) -> bool:
        """Verifica se a conexao esta ociosa por muito tempo."""
        return (time.time() - self.last_used) > max_idle_time


class ConnectionPool:
    """
    Pool de conexoes thread-safe para SQLite/Turso.

    Uso:
        pool = ConnectionPool('/path/to/db.db')

        with pool.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM users")
            results = cursor.fetchall()
    """

    def __init__(self, db_path: str, config: Optional[PoolConfig] = None):
        self.db_path = db_path
        self.config = config or PoolConfig()
        self._pool: Queue[PooledConnection] = Queue(maxsize=self.config.max_size)
        self._size = 0
        self._lock = threading.Lock()
        self._closed = False

        # Inicializar pool com conexoes minimas
        self._initialize_pool()

        # Iniciar thread de limpeza
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

        logger.info(f"ConnectionPool inicializado: {db_path} (min={self.config.min_size}, max={self.config.max_size})")

    def _initialize_pool(self):
        """Cria conexoes iniciais do pool."""
        for _ in range(self.config.min_size):
            try:
                conn = self._create_connection()
                self._pool.put_nowait(conn)
            except Exception as e:
                logger.warning(f"Falha ao criar conexao inicial: {e}")

    def _create_connection(self) -> PooledConnection:
        """Cria uma nova conexao."""
        with self._lock:
            if self._size >= self.config.max_size:
                raise RuntimeError("Pool atingiu tamanho maximo")

            connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=self.config.timeout
            )
            # Configuracoes de performance
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA cache_size=-64000")  # 64MB cache
            connection.execute("PRAGMA temp_store=MEMORY")

            self._size += 1
            logger.debug(f"Nova conexao criada. Pool size: {self._size}")

            return PooledConnection(connection, time.time())

    def _destroy_connection(self, pooled_conn: PooledConnection):
        """Destroi uma conexao."""
        try:
            pooled_conn.connection.close()
        except Exception as e:
            logger.warning(f"Erro ao fechar conexao: {e}")
        finally:
            with self._lock:
                self._size -= 1
            logger.debug(f"Conexao destruida. Pool size: {self._size}")

    @contextmanager
    def get_connection(self):
        """
        Context manager para obter conexao do pool.

        Yields:
            sqlite3.Connection: Conexao do banco

        Raises:
            TimeoutError: Se nao conseguir conexao no tempo limite
            RuntimeError: Se o pool estiver fechado
        """
        if self._closed:
            raise RuntimeError("Pool esta fechado")

        pooled_conn = None

        try:
            # Tentar obter conexao existente
            try:
                pooled_conn = self._pool.get(timeout=self.config.timeout)

                # Verificar saude da conexao
                if not pooled_conn.is_healthy():
                    logger.warning("Conexao nao saudavel, criando nova")
                    self._destroy_connection(pooled_conn)
                    pooled_conn = self._create_connection()

            except Empty:
                # Pool vazio, criar nova se possivel
                if self._size < self.config.max_size:
                    pooled_conn = self._create_connection()
                else:
                    raise TimeoutError(
                        f"Timeout obtendo conexao (max={self.config.max_size}, timeout={self.config.timeout}s)"
                    )

            pooled_conn.in_use = True
            pooled_conn.last_used = time.time()

            yield pooled_conn.connection

        except Exception as e:
            logger.error(f"Erro no pool: {e}")
            raise

        finally:
            # Devolver conexao ao pool
            if pooled_conn:
                pooled_conn.in_use = False
                pooled_conn.last_used = time.time()

                try:
                    self._pool.put_nowait(pooled_conn)
                except Full:
                    # Pool cheio, descartar conexao
                    self._destroy_connection(pooled_conn)

    def _cleanup_loop(self):
        """Loop de limpeza de conexoes ociosas."""
        while not self._closed:
            time.sleep(self.config.health_check_interval)

            if self._closed:
                break

            try:
                self._cleanup_idle_connections()
            except Exception as e:
                logger.error(f"Erro na limpeza do pool: {e}")

    def _cleanup_idle_connections(self):
        """Remove conexoes ociosas por muito tempo."""
        connections_to_check = []

        # Coletar conexoes do pool
        while True:
            try:
                conn = self._pool.get_nowait()
                connections_to_check.append(conn)
            except Empty:
                break

        # Verificar e devolver conexoes
        removed = 0
        for conn in connections_to_check:
            if conn.is_idle_too_long(self.config.max_idle_time) and self._size > self.config.min_size:
                self._destroy_connection(conn)
                removed += 1
            elif not conn.is_healthy():
                self._destroy_connection(conn)
                removed += 1
            else:
                try:
                    self._pool.put_nowait(conn)
                except Full:
                    self._destroy_connection(conn)
                    removed += 1

        if removed > 0:
            logger.info(f"Limpeza do pool: {removed} conexoes removidas")

    def close(self):
        """Fecha o pool e todas as conexoes."""
        self._closed = True

        while True:
            try:
                conn = self._pool.get_nowait()
                self._destroy_connection(conn)
            except Empty:
                break

        logger.info("ConnectionPool fechado")

    @property
    def stats(self) -> dict:
        """Retorna estatisticas do pool."""
        return {
            "size": self._size,
            "available": self._pool.qsize(),
            "max_size": self.config.max_size,
            "min_size": self.config.min_size,
            "closed": self._closed,
        }


# Singleton global do pool
_global_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()


def get_pool(db_path: Optional[str] = None) -> ConnectionPool:
    """
    Obtem o pool global de conexoes.

    Args:
        db_path: Caminho do banco (obrigatorio na primeira chamada)

    Returns:
        ConnectionPool: Pool de conexoes
    """
    global _global_pool

    with _pool_lock:
        if _global_pool is None:
            if db_path is None:
                raise ValueError("db_path obrigatorio na primeira chamada")
            _global_pool = ConnectionPool(db_path)

        return _global_pool


def close_pool():
    """Fecha o pool global."""
    global _global_pool

    with _pool_lock:
        if _global_pool:
            _global_pool.close()
            _global_pool = None
