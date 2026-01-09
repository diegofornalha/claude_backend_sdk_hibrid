"""
Embeddings Module - Sentence Transformers

Gera embeddings vetoriais para texto usando modelos locais.
Zero custo, baixa latencia.

Uso:
    from core.embeddings import create_embedding, create_embeddings_batch

    # Single embedding
    embedding = await create_embedding("texto para embedding")

    # Batch
    embeddings = await create_embeddings_batch(["texto1", "texto2"])
"""

import logging
from typing import List, Optional
import asyncio

logger = logging.getLogger(__name__)

# Modelo: all-MiniLM-L6-v2 (384 dimensoes)
# Alternativas:
# - all-mpnet-base-v2 (768 dims) - melhor qualidade, mais lento
# - paraphrase-multilingual-MiniLM-L12-v2 (384 dims) - suporte a portugues
MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

# Lazy loading do modelo
_model = None


def _get_model():
    """Carrega o modelo de embeddings (lazy loading)"""
    global _model

    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer

            logger.info(f"Carregando modelo de embeddings: {MODEL_NAME}")
            _model = SentenceTransformer(MODEL_NAME)
            logger.info(f"Modelo carregado com sucesso ({EMBEDDING_DIM} dimensoes)")

        except ImportError:
            logger.error(
                "sentence-transformers nao instalado. "
                "Execute: pip install sentence-transformers"
            )
            raise
        except Exception as e:
            logger.error(f"Erro ao carregar modelo: {e}")
            raise

    return _model


async def create_embedding(text: str) -> List[float]:
    """
    Cria embedding vetorial para um texto.

    Args:
        text: Texto para gerar embedding

    Returns:
        Lista de floats com 384 dimensoes

    Exemplo:
        embedding = await create_embedding("analise de residuo plastico")
        print(len(embedding))  # 384
    """
    if not text or not text.strip():
        logger.warning("Texto vazio recebido para embedding")
        return [0.0] * EMBEDDING_DIM

    model = _get_model()

    # Executar em thread pool para nao bloquear
    loop = asyncio.get_event_loop()
    embedding = await loop.run_in_executor(
        None,
        lambda: model.encode(text, convert_to_numpy=True).tolist()
    )

    return embedding


async def create_embeddings_batch(
    texts: List[str],
    batch_size: int = 32
) -> List[List[float]]:
    """
    Cria embeddings para multiplos textos em batch.

    Mais eficiente que chamar create_embedding() em loop.

    Args:
        texts: Lista de textos
        batch_size: Tamanho do batch para processamento

    Returns:
        Lista de embeddings (cada um com 384 dimensoes)

    Exemplo:
        texts = ["texto1", "texto2", "texto3"]
        embeddings = await create_embeddings_batch(texts)
        print(len(embeddings))  # 3
    """
    if not texts:
        return []

    # Filtrar textos vazios
    valid_texts = [t if t and t.strip() else "" for t in texts]

    model = _get_model()

    # Executar em thread pool
    loop = asyncio.get_event_loop()
    embeddings = await loop.run_in_executor(
        None,
        lambda: model.encode(
            valid_texts,
            batch_size=batch_size,
            convert_to_numpy=True,
            show_progress_bar=False
        ).tolist()
    )

    # Substituir embeddings de textos vazios por zeros
    for i, text in enumerate(texts):
        if not text or not text.strip():
            embeddings[i] = [0.0] * EMBEDDING_DIM

    return embeddings


def embedding_to_blob(embedding: List[float]) -> bytes:
    """
    Converte embedding para formato blob do Turso.

    Para usar com colunas FLOAT32[384] no Turso.

    Args:
        embedding: Lista de floats

    Returns:
        Bytes para armazenar no banco
    """
    import struct
    return struct.pack(f'{len(embedding)}f', *embedding)


def blob_to_embedding(blob: bytes) -> List[float]:
    """
    Converte blob do Turso de volta para lista de floats.

    Args:
        blob: Bytes do banco

    Returns:
        Lista de floats
    """
    import struct
    count = len(blob) // 4  # 4 bytes por float32
    return list(struct.unpack(f'{count}f', blob))


async def similarity_search_embedding(
    query: str,
    top_k: int = 10
) -> dict:
    """
    Prepara embedding para busca por similaridade.

    Retorna dict com embedding e metadata para usar em queries.

    Args:
        query: Texto da busca
        top_k: Numero de resultados desejados

    Returns:
        Dict com 'embedding' (blob) e 'top_k'
    """
    embedding = await create_embedding(query)

    return {
        'embedding': embedding,
        'embedding_blob': embedding_to_blob(embedding),
        'top_k': top_k,
        'query': query
    }


# Funcao de utilidade para obter dimensao
def get_embedding_dimension() -> int:
    """Retorna a dimensao dos embeddings (384)"""
    return EMBEDDING_DIM


# Funcao de utilidade para obter nome do modelo
def get_model_name() -> str:
    """Retorna o nome do modelo de embeddings"""
    return MODEL_NAME


# Health check
async def embeddings_health_check() -> dict:
    """
    Verifica se o sistema de embeddings esta funcionando.

    Returns:
        Dict com status e informacoes
    """
    try:
        # Testar criacao de embedding
        test_text = "teste de embedding"
        embedding = await create_embedding(test_text)

        return {
            "status": "healthy",
            "model": MODEL_NAME,
            "dimension": EMBEDDING_DIM,
            "test_embedding_size": len(embedding)
        }

    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }
