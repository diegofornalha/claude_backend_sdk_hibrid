"""
Sistema de Roles e Permissões
Decorators para verificar permissões de acesso por role
"""

from functools import wraps
from fastapi import HTTPException, status
from typing import List, Optional
from core.turso_database import get_db_connection


def get_user_role(user_id: int) -> Optional[str]:
    """
    Obtém o role efetivo de um usuário pelo ID, considerando hierarquia.

    Verifica:
    1. role na tabela users ("admin" ou "mentorado")
    2. admin_level (se definido, considera como admin)

    Args:
        user_id: ID do usuário

    Returns:
        Role efetivo ('admin', 'mentorado') ou None se não encontrado
    """
    from core.auth import get_effective_role
    try:
        return get_effective_role(user_id)
    except Exception as e:
        print(f"Erro ao obter role do usuário: {e}")
        return None


def get_user_mentor_id(user_id: int) -> Optional[int]:
    """
    Obtém o mentor_id de um usuário

    DESCONTINUADO: Funcionalidade de mentor removida

    Args:
        user_id: ID do usuário

    Returns:
        Sempre None (mentor_id removido do schema)
    """
    return None


def require_role(allowed_roles: List[str]):
    """
    Decorator para verificar se o usuário tem o role necessário

    Args:
        allowed_roles: Lista de roles permitidos ['admin', 'mentor', 'mentorado']

    Usage:
        @app.get("/admin/endpoint")
        @require_role(['admin'])
        async def admin_endpoint(user_id: int = Depends(get_current_user)):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extrair user_id dos kwargs
            user_id = kwargs.get('user_id')

            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Autenticação necessária"
                )

            # Verificar role do usuário
            user_role = get_user_role(user_id)

            if not user_role:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Usuário não encontrado"
                )

            if user_role not in allowed_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Acesso negado. Requer role: {', '.join(allowed_roles)}"
                )

            # Adicionar role aos kwargs para uso na função
            kwargs['user_role'] = user_role

            return await func(*args, **kwargs)

        return wrapper
    return decorator


def require_admin(func):
    """
    Decorator para endpoints que requerem role 'admin'
    Atalho para @require_role(['admin'])
    """
    return require_role(['admin'])(func)


def require_mentor(func):
    """
    Decorator para endpoints que requerem role 'mentor' ou 'admin'
    """
    return require_role(['admin', 'mentor'])(func)


def can_access_mentorado(user_id: int, user_role: str, mentorado_id: int) -> bool:
    """
    Verifica se o usuário pode acessar dados de um mentorado específico.

    Usa hierarquia para verificar permissões granulares.

    Args:
        user_id: ID do usuário fazendo a requisição
        user_role: Role do usuário ('admin', 'mentor', 'mentorado')
        mentorado_id: ID do mentorado que está sendo acessado

    Returns:
        True se pode acessar, False caso contrário
    """
    # Admin pode acessar qualquer um
    if user_role == 'admin':
        return True

    # Mentorado só pode acessar a si mesmo
    if user_role == 'mentorado':
        return user_id == mentorado_id

    # Verificar pela hierarquia se pode gerenciar
    from core.auth import can_manage_user
    if can_manage_user(user_id, mentorado_id):
        return True

    return False


def get_mentorados_ids(mentor_id: int) -> List[int]:
    """
    Obtém lista de IDs dos mentorados de um mentor

    DESCONTINUADO: Funcionalidade de mentor removida

    Args:
        mentor_id: ID do mentor

    Returns:
        Lista vazia (mentor_id removido do schema)
    """
    return []


def filter_sql_by_role(user_id: int, user_role: str, query: str) -> tuple[str, list]:
    """
    Filtra uma query SQL baseado no role do usuário

    SEGURANÇA: Retorna query parametrizada para prevenir SQL injection

    Para mentores: adiciona WHERE para filtrar apenas seus mentorados
    Para mentorados: adiciona WHERE para filtrar apenas seus próprios dados
    Para admin: retorna query sem modificação

    Args:
        user_id: ID do usuário
        user_role: Role do usuário
        query: Query SQL original

    Returns:
        Tupla (query_filtrada, parametros)
    """
    if user_role == 'admin':
        return query, []

    # Para mentorados, filtrar por user_id
    if user_role == 'mentorado':
        # Se a query já tem WHERE, adicionar AND
        if 'WHERE' in query.upper():
            filtered = query.replace('WHERE', 'WHERE users.user_id = ? AND', 1)
            return filtered, [user_id]
        # Se não tem WHERE, adicionar
        else:
            filtered = query + ' WHERE users.user_id = ?'
            return filtered, [user_id]

    # Para mentores, filtrar por mentor_id
    if user_role == 'mentor':
        if 'WHERE' in query.upper():
            filtered = query.replace('WHERE', 'WHERE users.mentor_id = ? AND', 1)
            return filtered, [user_id]
        else:
            filtered = query + ' WHERE users.mentor_id = ?'
            return filtered, [user_id]

    return query, []
