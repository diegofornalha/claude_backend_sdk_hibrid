"""
Turso/libSQL Database Module - Embedded + Sync Pattern

Usa embedded replica local com sync automático para Turso Cloud.
Padrão recomendado pela documentação Turso para AI Agents.

Configuracao via .env:
    TURSO_DATABASE_PATH=./crm.db                              # Embedded local
    TURSO_SYNC_URL=https://context-memory-diegofornalha.turso.io  # Cloud sync
    TURSO_AUTH_TOKEN=your-token                                 # Auth token
    TURSO_SYNC_INTERVAL=5                                       # Sync a cada 5s (opcional)

Benefícios:
    - Latência zero para leituras (local)
    - Offline capability (funciona sem internet)
    - Background sync automático para cloud
    - Persistent memory (dados sobrevivem a restarts)

Uso:
    from core.turso_database import db

    # Sync
    users = db.query("SELECT * FROM users WHERE id = ?", (1,))
    db.execute("INSERT INTO users (name) VALUES (?)", ("Joao",))

    # Async
    users = await db.query_async("SELECT * FROM users")
"""

import os
import logging
from typing import Optional, List, Dict, Any, Tuple, Union

from dotenv import load_dotenv
import libsql_experimental

# Carregar .env antes de ler variaveis
load_dotenv()

logger = logging.getLogger(__name__)

# Configuracao - Embedded + Sync Pattern
TURSO_DATABASE_PATH = os.getenv("TURSO_DATABASE_PATH", "./crm.db")
TURSO_SYNC_URL = os.getenv("TURSO_SYNC_URL", "")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")
TURSO_SYNC_INTERVAL = int(os.getenv("TURSO_SYNC_INTERVAL", "5"))  # segundos


class TursoDatabase:
    """
    Cliente Embedded + Sync para Turso/libSQL.

    Usa embedded replica local com sync automático para cloud.
    Padrão recomendado para AI Agents.

    Benefícios:
    - Latência zero para leituras (dados locais)
    - Offline capable (funciona sem internet)
    - Background sync automático
    - Persistent memory across restarts
    """

    def __init__(self):
        self._db_path = TURSO_DATABASE_PATH
        self._sync_url = TURSO_SYNC_URL
        self._auth_token = TURSO_AUTH_TOKEN
        self._sync_interval = TURSO_SYNC_INTERVAL

        if not self._sync_url:
            logger.warning(
                "TURSO_SYNC_URL not configured. "
                "Running in local-only mode without cloud sync."
            )
            self._mode = "local-only"
        else:
            self._mode = "embedded-sync"

        logger.info(f"Turso Database: {self._mode} mode")
        logger.info(f"Local path: {self._db_path}")
        if self._sync_url:
            logger.info(f"Sync URL: {self._sync_url[:50]}...")
            logger.info(f"Sync interval: {self._sync_interval}s")

    def _get_connection(self):
        """Cria conexão embedded com sync"""
        if self._mode == "embedded-sync":
            # Embedded + Sync pattern (recomendado)
            return libsql_experimental.connect(
                database=self._db_path,
                sync_url=self._sync_url,
                sync_interval=self._sync_interval * 1000,  # converter para ms
                auth_token=self._auth_token,
                check_same_thread=False  # Permitir uso em threads diferentes
            )
        else:
            # Local-only (fallback sem sync)
            return libsql_experimental.connect(
                database=self._db_path,
                check_same_thread=False
            )

    # ==========================================================================
    # MÉTODOS DE COMPATIBILIDADE (para session_manager.py e código legado)
    # ==========================================================================

    def cursor(self, dictionary: bool = False):
        """Retorna cursor-like object (compatibilidade)"""
        return TursoCursorWrapper(self, dictionary=dictionary)

    def commit(self):
        """Commit (no-op para compatibilidade - libsql auto-commits)"""
        pass

    def rollback(self):
        """Rollback (no-op para compatibilidade)"""
        pass

    def close(self):
        """Close (no-op para compatibilidade)"""
        pass

    # ==========================================================================
    # METODOS SINCRONOS (compatibilidade com codigo existente)
    # ==========================================================================

    def query(
        self,
        sql: str,
        params: Union[Tuple, List] = ()
    ) -> List[Dict[str, Any]]:
        """
        Executa SELECT e retorna lista de dicts.

        Exemplo:
            users = db.query("SELECT * FROM users WHERE id = ?", (1,))
        """
        # Converter %s para ? (compatibilidade MySQL)
        sql = sql.replace('%s', '?')
        params_tuple = tuple(params) if params else ()

        # Usar embedded connection (SQLite-compatible API)
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params_tuple)

        # Converter para lista de dicts
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = []
        for row in cursor.fetchall():
            rows.append(dict(zip(columns, row)))

        cursor.close()
        conn.close()

        return rows

    def execute(
        self,
        sql: str,
        params: Union[Tuple, List] = ()
    ) -> int:
        """
        Executa INSERT/UPDATE/DELETE e retorna rows affected.

        Exemplo:
            rows = db.execute("UPDATE users SET name = ? WHERE id = ?", ("Nome", 1))
        """
        sql = sql.replace('%s', '?')
        params_tuple = tuple(params) if params else ()

        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params_tuple)
        rows_affected = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()

        return rows_affected


class TursoCursorWrapper:
    """Wrapper que simula cursor SQLite para compatibilidade com session_manager"""

    def __init__(self, db: TursoDatabase, dictionary: bool = False):
        self._db = db
        self._results = []
        self._description = None
        self._rowcount = 0
        self._dictionary = dictionary
        self._columns = []
        self._lastrowid = None

    def execute(self, sql: str, params: Union[Tuple, List] = ()):
        """Executa query"""
        from datetime import datetime, date
        
        sql = sql.replace('%s', '?')
        params_tuple = tuple(params) if params else ()
        
        # Converter objetos datetime/date para strings (SQLite não aceita diretamente)
        converted_params = []
        for param in params_tuple:
            if isinstance(param, datetime):
                converted_params.append(param.strftime('%Y-%m-%d %H:%M:%S'))
            elif isinstance(param, date):
                converted_params.append(param.strftime('%Y-%m-%d'))
            else:
                converted_params.append(param)
        params_tuple = tuple(converted_params)

        conn = self._db._get_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params_tuple)

        # Armazenar resultados
        self._description = cursor.description
        self._rowcount = cursor.rowcount
        self._lastrowid = cursor.lastrowid
        self._results = cursor.fetchall()

        # Extrair nomes das colunas para modo dictionary
        if self._description:
            self._columns = [desc[0] for desc in self._description]
        else:
            self._columns = []

        # Commit para queries de escrita (INSERT, UPDATE, DELETE)
        sql_upper = sql.strip().upper()
        if sql_upper.startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'ALTER')):
            conn.commit()

        cursor.close()
        conn.close()

    def _to_dict(self, row):
        """Converte uma linha (tupla) para dicionário"""
        if row is None:
            return None
        return dict(zip(self._columns, row))

    def fetchall(self):
        """Retorna todos os resultados"""
        if self._dictionary and self._columns:
            return [self._to_dict(row) for row in self._results]
        return self._results

    def fetchone(self):
        """Retorna primeiro resultado"""
        if not self._results:
            return None
        row = self._results[0]
        if self._dictionary and self._columns:
            return self._to_dict(row)
        return row

    def close(self):
        """Fecha cursor"""
        pass

    @property
    def description(self):
        return self._description

    @property
    def rowcount(self):
        return self._rowcount

    @property
    def lastrowid(self):
        return self._lastrowid


# ==============================================================================
# Instancia global e funcoes de conveniencia
# ==============================================================================

# Instancia global do banco
db = TursoDatabase()


def get_db_connection():
    """Retorna instancia do banco (compatibilidade)"""
    return db


# Aliases
execute_query = db.query
execute_write = db.execute
query = db.query
write = db.execute


# Config para compatibilidade
DB_CONFIG = {
    'mode': db._mode,
    'path': db._db_path,
    'sync_url': db._sync_url,
    'sync_interval': db._sync_interval
}

db_pool = None  # Turso embedded nao usa pool


async def health_check() -> Dict[str, Any]:
    """Verifica saude do banco de dados."""
    try:
        # Executar query simples para testar
        result = db.query("SELECT 1 as health_check")

        # Contar tabelas
        tables = db.query("SELECT name FROM sqlite_master WHERE type='table'")

        return {
            "status": "healthy",
            "mode": db._mode,
            "tables": len(tables),
            "database_path": db._db_path
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }
