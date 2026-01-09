"""
Lead Conversion Routes - Conversão de leads para mentorados

Quando um lead compra e vira cliente, ele é convertido para mentorado
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Header
from datetime import datetime
from typing import Optional

from core.turso_database import get_db_connection
from core.auth import verify_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/leads", tags=["lead-conversion"])


async def get_user_from_token(authorization: Optional[str] = Header(None)) -> int:
    """Extract user ID from JWT token in Authorization header"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    # Remove "Bearer " prefix if present
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization

    user_id = verify_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return user_id


def get_user_role(user_id: int) -> str:
    """
    Helper para obter role efetivo do usuário considerando hierarquia.
    """
    from core.auth import get_effective_role
    try:
        return get_effective_role(user_id)
    except Exception as e:
        logger.error(f"Erro ao obter role: {e}")
        return None


@router.put("/{lead_id}/convert-to-mentorado")
async def convert_lead_to_mentorado(
    lead_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Converte um lead para mentorado (cliente que comprou)

    Mudanças:
    - role: 'lead' → 'mentorado'
    - account_status: 'lead' → 'active'
    - Estado CRM: atualizado para 'produto_vendido'
    - Evento registrado
    """
    # Verificar se é admin
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se lead existe
        cursor.execute("SELECT role, username, email FROM users WHERE user_id = ?", (lead_id,))
        lead = cursor.fetchone()

        if not lead:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Lead não encontrado")

        if lead['role'] != 'lead':
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail=f"Usuário já é {lead['role']}, não é um lead")

        # Converter para mentorado
        cursor.execute("""
            UPDATE users
            SET role = 'mentorado', account_status = 'active'
            WHERE user_id = ?
        """, (lead_id,))

        # Atualizar estado CRM
        now = datetime.now()
        cursor.execute("""
            UPDATE crm_lead_state
            SET current_state = 'produto_vendido',
                state_updated_at = ?
            WHERE lead_id = ?
        """, (now, lead_id))

        # Registrar evento
        import json
        event_payload = json.dumps({
            "converted_by": user_id,
            "converted_at": now.isoformat(),
            "old_role": "lead",
            "new_role": "mentorado"
        })

        cursor.execute("""
            INSERT INTO crm_lead_events (lead_id, event_type, event_at, channel, actor_type, actor_id, payload)
            VALUES (?, 'lead_converted_to_client', ?, 'crm', 'admin', ?, ?)
        """, (lead_id, now, user_id, event_payload))

        conn.commit()
        cursor.close()
        conn.close()

        logger.info(f"✅ Lead {lead_id} ({lead['email']}) convertido para mentorado por admin {user_id}")

        return {
            "success": True,
            "message": f"Lead '{lead['username']}' convertido para mentorado com sucesso",
            "lead_id": lead_id,
            "new_role": "mentorado"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao converter lead {lead_id}: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{lead_id}/revert-to-lead")
async def revert_mentorado_to_lead(
    mentorado_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Reverte um mentorado para lead

    Mudanças:
    - role: 'mentorado' → 'lead'
    - account_status: 'active' → 'lead'
    - Estado CRM: atualizado para 'novo'
    """
    # Verificar se é admin
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se mentorado existe
        cursor.execute("SELECT role, username, email FROM users WHERE user_id = ?", (mentorado_id,))
        mentorado = cursor.fetchone()

        if not mentorado:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Mentorado não encontrado")

        if mentorado['role'] != 'mentorado':
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail=f"Usuário é {mentorado['role']}, não é mentorado")

        # Reverter para lead
        cursor.execute("""
            UPDATE users
            SET role = 'lead', account_status = 'lead'
            WHERE user_id = ?
        """, (mentorado_id,))

        # Criar/atualizar estado CRM
        cursor.execute("""
            INSERT INTO crm_lead_state (lead_id, current_state, owner_team, state_updated_at)
            VALUES (?, 'novo', 'marketing', ?)
            ON CONFLICT(lead_id) DO UPDATE SET
                current_state = 'novo',
                state_updated_at = ?
        """, (mentorado_id, datetime.now(), datetime.now()))

        # Registrar evento
        import json
        event_payload = json.dumps({
            "reverted_by": user_id,
            "reverted_at": datetime.now().isoformat(),
            "old_role": "mentorado",
            "new_role": "lead"
        })

        cursor.execute("""
            INSERT INTO crm_lead_events (lead_id, event_type, event_at, channel, actor_type, actor_id, payload)
            VALUES (?, 'mentorado_reverted_to_lead', ?, 'crm', 'admin', ?, ?)
        """, (mentorado_id, datetime.now(), user_id, event_payload))

        conn.commit()
        cursor.close()
        conn.close()

        logger.info(f"✅ Mentorado {mentorado_id} ({mentorado['email']}) revertido para lead por admin {user_id}")

        return {
            "success": True,
            "message": f"Mentorado '{mentorado['username']}' revertido para lead com sucesso",
            "user_id": mentorado_id,
            "new_role": "lead"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao reverter mentorado {mentorado_id}: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))
