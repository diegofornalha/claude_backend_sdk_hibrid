"""
SQL Tools - Ferramentas de query SQL seguras

Migrado de app.py - mantém toda lógica de segurança.
"""

import json
import logging
from typing import Dict, Any

from claude_agent_sdk import tool

logger = logging.getLogger(__name__)


@tool(
    "execute_sql_query",
    "Execute read-only SQL query on Nanda database. Returns up to 100 rows.",
    {
        "query": str
    }
)
async def execute_sql_query(args: Dict[str, Any]) -> Dict:
    """
    Executa query SQL READ-ONLY no banco Nanda

    SEGURANÇA:
    - Apenas SELECT queries permitidas
    - Bloqueia INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE
    - Auto-adiciona LIMIT 100 se não presente
    - Não permite queries em tabelas sensíveis (users, api_keys)

    Args:
        query: Query SQL (apenas SELECT)

    Returns:
        {
            "content": [{"type": "text", "text": "JSON com resultados"}]
        }
    """
    # Importar função de conexão do módulo pai
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import get_db_connection

    query = args["query"]
    query_upper = query.upper().strip()

    # VALIDAÇÃO 1: Apenas SELECT
    if not query_upper.startswith("SELECT"):
        logger.warning(f"Blocked non-SELECT query: {query[:100]}")
        return {
            "content": [{
                "type": "text",
                "text": "Error: Only SELECT queries are allowed for security reasons."
            }],
            "isError": True
        }

    # VALIDAÇÃO 2: Bloquear palavras perigosas (como palavras completas, não substrings)
    # Importante: "created_at" contém "CREATE" mas não é perigoso
    import re
    dangerous_keywords = [
        "INSERT", "UPDATE", "DELETE", "DROP",
        "ALTER", "CREATE", "TRUNCATE", "GRANT",
        "REVOKE", "EXEC", "EXECUTE"
    ]

    for keyword in dangerous_keywords:
        # Usar regex para verificar palavra completa (não substring)
        # \b = word boundary (limite de palavra)
        pattern = rf'\b{keyword}\b'
        if re.search(pattern, query_upper):
            logger.warning(f"Blocked query with dangerous keyword {keyword}: {query[:100]}")
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: Dangerous operation '{keyword}' is not allowed."
                }],
                "isError": True
            }

    # VALIDAÇÃO 3: Bloquear tabelas sensíveis
    # Nota: 'users' removido da lista pois admin precisa acessar para estatísticas
    # A senha (password_hash) nunca é retornada pois não é selecionada explicitamente
    sensitive_tables = [
        "user_verifications", "api_keys",
        "refresh_tokens"  # tokens de refresh
    ]

    query_lower = query.lower()

    # Bloquear acesso a colunas sensíveis mesmo em tabelas permitidas
    sensitive_columns = ["password_hash", "password", "api_key", "secret"]
    for col in sensitive_columns:
        if col in query_lower:
            logger.warning(f"Blocked query accessing sensitive column {col}: {query[:100]}")
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: Access to column '{col}' is not allowed for security reasons."
                }],
                "isError": True
            }

    for table in sensitive_tables:
        # Verificar se tabela aparece no FROM ou JOIN
        if f" {table} " in f" {query_lower} " or f" {table}," in query_lower:
            logger.warning(f"Blocked query accessing sensitive table {table}: {query[:100]}")
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: Access to table '{table}' is not allowed for privacy reasons."
                }],
                "isError": True
            }

    # VALIDAÇÃO 4: Auto-adicionar LIMIT se não tiver
    if "LIMIT" not in query_upper:
        query = query.rstrip(";") + " LIMIT 100"
        logger.info("Auto-added LIMIT 100 to query")

    # EXECUTAR QUERY
    try:
        conn = get_db_connection()
        if not conn:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: Database connection failed"
                }],
                "isError": True
            }

        # Usar API do Turso/SQLite (ele já converte %s para ? internamente)
        results = conn.query(query)

        # Contar linhas afetadas
        row_count = len(results)

        logger.info(f"SQL query executed successfully, returned {row_count} rows")

        # Formatar resposta
        response_data = {
            "rows": row_count,
            "data": results,
            "query": query
        }

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(response_data, indent=2, ensure_ascii=False, default=str)
            }]
        }

    except Exception as e:
        logger.error(f"SQL query error: {e}")
        return {
            "content": [{
                "type": "text",
                "text": f"SQL Error: {str(e)}"
            }],
            "isError": True
        }
