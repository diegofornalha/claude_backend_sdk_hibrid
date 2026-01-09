# Core modules exports

# Embeddings
from core.embeddings import (
    create_embedding,
    create_embeddings_batch,
    embedding_to_blob,
    blob_to_embedding,
    get_embedding_dimension,
    embeddings_health_check,
)

# Vector Search
from core.vector_search import (
    VectorSearch,
    get_vector_search,
    vector_search_health_check,
)

# Database
from core.turso_database import (
    TursoDatabase,
    get_db_connection,
    db,
)

# Session Manager
from core.session_manager import SessionManager

__all__ = [
    # Embeddings
    'create_embedding',
    'create_embeddings_batch',
    'embedding_to_blob',
    'blob_to_embedding',
    'get_embedding_dimension',
    'embeddings_health_check',
    # Vector Search
    'VectorSearch',
    'get_vector_search',
    'vector_search_health_check',
    # Database
    'TursoDatabase',
    'get_db_connection',
    'db',
    # Session Manager
    'SessionManager',
]
