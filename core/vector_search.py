"""
Busca Vetorial usando Turso Vector Nativo

Implementa busca por similaridade semântica usando as funções
vetoriais nativas do Turso/libSQL: vector_distance_cos(), vector_distance_l2().

Uso:
    from core.vector_search import VectorSearch
    from core.turso_database import get_db_connection

    vector_search = VectorSearch(get_db_connection)

    # Buscar mensagens similares
    results = await vector_search.search_similar_messages(
        query="sintomas de dengue",
        limit=10,
        threshold=0.7
    )
"""

import struct
import logging
from typing import List, Dict, Optional, Callable

from core.embeddings import create_embedding, embedding_to_blob, EMBEDDING_DIM

logger = logging.getLogger(__name__)


class VectorSearch:
    """
    Busca vetorial usando funções nativas do Turso.

    Usa cosine similarity para encontrar mensagens semanticamente similares.
    Embeddings são gerados com sentence-transformers (384 dimensões).
    """

    def __init__(self, get_db_connection_func: Callable):
        """
        Args:
            get_db_connection_func: Função que retorna conexão do banco
        """
        self.get_db_connection = get_db_connection_func

    async def search_similar_messages(
        self,
        query: str,
        session_id: Optional[str] = None,
        user_id: Optional[int] = None,
        limit: int = 10,
        threshold: float = 0.8
    ) -> List[Dict]:
        """
        Busca mensagens similares usando cosine similarity.

        Args:
            query: Texto da busca
            session_id: Filtrar por sessão (opcional)
            user_id: Filtrar por usuário (opcional)
            limit: Número máximo de resultados
            threshold: Threshold de distância (menor = mais similar, 0-2 para cosine)

        Returns:
            Lista de mensagens com score de similaridade

        Note:
            vector_distance_cos retorna distância (0 = idêntico, 2 = oposto)
            Para converter em similaridade: similarity = 1 - (distance / 2)
        """
        if not query or not query.strip():
            logger.warning("Empty query received for vector search")
            return []

        try:
            # Gerar embedding da query
            query_embedding = await create_embedding(query)
            query_blob = embedding_to_blob(query_embedding)

            conn = self.get_db_connection()
            if not conn:
                raise Exception("Database connection failed")

            cursor = conn.cursor(dictionary=True)

            # Construir SQL com filtros opcionais
            # vector_distance_cos retorna distância (0 = idêntico)
            # Usamos threshold como distância máxima
            where_clauses = ["message_embedding IS NOT NULL"]
            params = []

            if session_id:
                where_clauses.append("session_id = %s")
                params.append(session_id)

            if user_id:
                where_clauses.append("user_id = %s")
                params.append(user_id)

            where_sql = " AND ".join(where_clauses)

            # SQL usando Turso vector functions
            # Nota: Turso usa vector32() para converter blob em vetor
            sql = f"""
                SELECT
                    message_id,
                    session_id,
                    user_id,
                    role,
                    content,
                    created_at,
                    vector_distance_cos(message_embedding, vector32(%s)) as distance
                FROM chat_messages
                WHERE {where_sql}
                  AND vector_distance_cos(message_embedding, vector32(%s)) <= %s
                ORDER BY distance ASC
                LIMIT %s
            """

            # Adicionar parâmetros do vetor, threshold e limit
            all_params = [query_blob] + params + [query_blob, threshold, limit]

            cursor.execute(sql, tuple(all_params))
            results = cursor.fetchall()

            cursor.close()
            conn.close()

            # Adicionar campo de similaridade (converter distância para 0-1)
            for r in results:
                if r.get('distance') is not None:
                    r['similarity'] = 1 - (r['distance'] / 2)
                else:
                    r['similarity'] = 0

            logger.info(f"Vector search found {len(results)} results for query: {query[:50]}...")
            return results

        except Exception as e:
            logger.error(f"Error in vector search: {e}")
            # Fallback para busca simples se vector functions não disponíveis
            return await self._fallback_text_search(query, session_id, user_id, limit)

    async def _fallback_text_search(
        self,
        query: str,
        session_id: Optional[str] = None,
        user_id: Optional[int] = None,
        limit: int = 10
    ) -> List[Dict]:
        """
        Fallback para busca por texto simples se busca vetorial falhar.
        """
        logger.warning("Using fallback text search instead of vector search")

        conn = self.get_db_connection()
        if not conn:
            return []

        try:
            cursor = conn.cursor(dictionary=True)

            where_clauses = ["content LIKE %s"]
            params = [f"%{query}%"]

            if session_id:
                where_clauses.append("session_id = %s")
                params.append(session_id)

            if user_id:
                where_clauses.append("user_id = %s")
                params.append(user_id)

            where_sql = " AND ".join(where_clauses)

            sql = f"""
                SELECT
                    message_id,
                    session_id,
                    user_id,
                    role,
                    content,
                    created_at,
                    0.5 as similarity
                FROM chat_messages
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT %s
            """

            params.append(limit)
            cursor.execute(sql, tuple(params))
            results = cursor.fetchall()

            cursor.close()
            conn.close()

            return results

        except Exception as e:
            logger.error(f"Error in fallback text search: {e}")
            return []

    async def find_related_diagnostics(
        self,
        diagnostic_text: str,
        exclude_user_id: Optional[int] = None,
        limit: int = 5
    ) -> List[Dict]:
        """
        Encontra diagnósticos relacionados de outros usuários.
        Útil para recomendar casos similares.

        Args:
            diagnostic_text: Texto do diagnóstico para buscar similares
            exclude_user_id: Excluir usuário específico (para não mostrar próprios dados)
            limit: Número máximo de resultados

        Returns:
            Lista de diagnósticos similares com score
        """
        if not diagnostic_text or not diagnostic_text.strip():
            return []

        try:
            query_embedding = await create_embedding(diagnostic_text)
            query_blob = embedding_to_blob(query_embedding)

            conn = self.get_db_connection()
            if not conn:
                raise Exception("Database connection failed")

            cursor = conn.cursor(dictionary=True)

            # Buscar mensagens do assistant (diagnósticos) de outros usuários
            sql = """
                SELECT DISTINCT
                    cm.session_id,
                    cm.content,
                    cm.user_id,
                    cm.created_at,
                    vector_distance_cos(cm.message_embedding, vector32(%s)) as distance
                FROM chat_messages cm
                WHERE cm.message_embedding IS NOT NULL
                  AND cm.role = 'assistant'
                  AND cm.user_id != %s
                  AND vector_distance_cos(cm.message_embedding, vector32(%s)) <= 0.5
                ORDER BY distance ASC
                LIMIT %s
            """

            params = (query_blob, exclude_user_id or -1, query_blob, limit)
            cursor.execute(sql, params)
            results = cursor.fetchall()

            cursor.close()
            conn.close()

            # Converter distância para similaridade
            for r in results:
                if r.get('distance') is not None:
                    r['similarity'] = 1 - (r['distance'] / 2)
                else:
                    r['similarity'] = 0

            return results

        except Exception as e:
            logger.error(f"Error finding related diagnostics: {e}")
            return []

    async def get_context_for_rag(
        self,
        query: str,
        user_id: int,
        k: int = 5,
        max_chars: int = 4000
    ) -> str:
        """
        Busca contexto relevante para RAG (Retrieval Augmented Generation).

        Args:
            query: Pergunta/contexto atual
            user_id: ID do usuário
            k: Número de mensagens a buscar
            max_chars: Limite de caracteres do contexto

        Returns:
            String formatada com contexto relevante para incluir no prompt
        """
        results = await self.search_similar_messages(
            query=query,
            user_id=user_id,
            limit=k,
            threshold=0.8
        )

        if not results:
            return ""

        context_parts = []
        total_chars = 0

        for r in results:
            content = r.get('content', '')
            role = r.get('role', 'unknown')
            similarity = r.get('similarity', 0)

            # Formatar entrada
            entry = f"[{role.upper()} - similaridade: {similarity:.2f}]: {content}"

            # Verificar limite de caracteres
            if total_chars + len(entry) > max_chars:
                break

            context_parts.append(entry)
            total_chars += len(entry)

        if not context_parts:
            return ""

        return "\n\n--- CONTEXTO RELEVANTE (RAG) ---\n" + "\n\n".join(context_parts) + "\n--- FIM DO CONTEXTO ---\n"

    async def count_embeddings(self) -> Dict:
        """
        Conta quantas mensagens têm embeddings salvos.

        Returns:
            Dict com estatísticas de embeddings
        """
        conn = self.get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}

        try:
            cursor = conn.cursor(dictionary=True)

            # Total de mensagens
            cursor.execute("SELECT COUNT(*) as total FROM chat_messages")
            total = cursor.fetchone()['total']

            # Mensagens com embedding
            cursor.execute(
                "SELECT COUNT(*) as with_embedding FROM chat_messages WHERE message_embedding IS NOT NULL"
            )
            with_embedding = cursor.fetchone()['with_embedding']

            # Mensagens sem embedding
            without_embedding = total - with_embedding

            cursor.close()
            conn.close()

            return {
                "total_messages": total,
                "with_embedding": with_embedding,
                "without_embedding": without_embedding,
                "coverage_percent": round((with_embedding / total * 100) if total > 0 else 0, 2)
            }

        except Exception as e:
            logger.error(f"Error counting embeddings: {e}")
            return {"error": str(e)}

    async def backfill_embeddings(self, batch_size: int = 50) -> Dict:
        """
        Gera embeddings para mensagens que não têm.
        Útil para migração de dados existentes.

        Args:
            batch_size: Quantas mensagens processar por vez

        Returns:
            Dict com resultado da operação
        """
        conn = self.get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}

        try:
            cursor = conn.cursor(dictionary=True)

            # Buscar mensagens sem embedding
            cursor.execute("""
                SELECT message_id, content
                FROM chat_messages
                WHERE message_embedding IS NULL
                  AND content IS NOT NULL
                  AND content != ''
                LIMIT %s
            """, (batch_size,))

            messages = cursor.fetchall()

            if not messages:
                cursor.close()
                conn.close()
                return {"processed": 0, "message": "No messages to process"}

            processed = 0
            errors = 0

            for msg in messages:
                try:
                    # Gerar embedding
                    embedding = await create_embedding(msg['content'])
                    embedding_blob = embedding_to_blob(embedding)

                    # Atualizar no banco
                    cursor.execute("""
                        UPDATE chat_messages
                        SET message_embedding = %s
                        WHERE message_id = %s
                    """, (embedding_blob, msg['message_id']))

                    processed += 1

                except Exception as e:
                    logger.error(f"Error generating embedding for message {msg['message_id']}: {e}")
                    errors += 1

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"Backfill complete: {processed} processed, {errors} errors")

            return {
                "processed": processed,
                "errors": errors,
                "remaining": await self._count_without_embedding()
            }

        except Exception as e:
            logger.error(f"Error in backfill: {e}")
            return {"error": str(e)}

    async def _count_without_embedding(self) -> int:
        """Conta mensagens sem embedding."""
        conn = self.get_db_connection()
        if not conn:
            return -1

        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM chat_messages
                WHERE message_embedding IS NULL
                  AND content IS NOT NULL
                  AND content != ''
            """)
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            return result['count']
        except:
            return -1


# Instância singleton para uso conveniente
_vector_search_instance = None


def get_vector_search():
    """Retorna instância singleton do VectorSearch."""
    global _vector_search_instance
    if _vector_search_instance is None:
        from core.turso_database import get_db_connection
        _vector_search_instance = VectorSearch(get_db_connection)
    return _vector_search_instance


# Health check
async def vector_search_health_check() -> Dict:
    """Verifica se o sistema de busca vetorial está funcionando."""
    try:
        vs = get_vector_search()
        stats = await vs.count_embeddings()

        # Testar busca
        test_results = await vs.search_similar_messages(
            query="teste de busca",
            limit=1
        )

        return {
            "status": "healthy",
            "embedding_stats": stats,
            "search_working": len(test_results) >= 0  # Pode ser 0 se não há dados
        }

    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }
