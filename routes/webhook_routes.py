"""
Webhook Routes - Endpoints para receber webhooks externos

Recebe dados de:
- Elementor Forms (captura de leads)
- Typeform (pesquisa de diagnóstico)
- Google Calendar (eventos de reunião)
"""

import logging
import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any
from datetime import datetime

from core.crm_agent_orchestrator import get_orchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# =============================================================================
# Modelos de Request
# =============================================================================

class ElementorWebhook(BaseModel):
    """Dados do webhook do Elementor Forms"""
    nome: str
    email: EmailStr
    telefone: Optional[str] = None
    profissao: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    utm_term: Optional[str] = None
    ip_address: Optional[str] = None
    landing_page_url: Optional[str] = None
    form_name: Optional[str] = None
    captured_at: Optional[str] = None


class TypeformWebhook(BaseModel):
    """Dados do webhook do Typeform"""
    email: EmailStr
    respostas: Dict[str, Any]
    typeform_response_id: Optional[str] = None
    submitted_at: Optional[str] = None


# =============================================================================
# Background Tasks
# =============================================================================

async def process_lead_background(lead_id: int):
    """
    Processa lead em background usando Claude Agent SDK.

    Esta task roda de forma assíncrona após o webhook retornar sucesso.
    """
    try:
        logger.info(f"🔄 Processando lead {lead_id} em background...")

        orchestrator = get_orchestrator()
        result = await orchestrator.process_new_lead(lead_id)

        if result.get("success"):
            logger.info(f"✅ Lead {lead_id} processado: score={result.get('score')}, temp={result.get('temperatura')}")
        else:
            logger.error(f"❌ Falha ao processar lead {lead_id}: {result.get('error')}")

    except Exception as e:
        logger.error(f"❌ Erro crítico ao processar lead {lead_id}: {e}")


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/elementor")
async def elementor_webhook(
    data: ElementorWebhook,
    background_tasks: BackgroundTasks
):
    """
    Recebe webhook do Elementor Forms.

    Fluxo:
    1. Captura lead via MCP (capture_lead_from_elementor)
    2. Retorna sucesso imediatamente
    3. Processa lead em background (scoring + tasks + agenda)
    """
    try:
        # 1. Capturar lead manualmente no banco (temporário até MCP funcionar via SDK)
        from core.turso_database import get_db_connection

        logger.info(f"📥 Webhook recebido: {data.nome} ({data.email})")

        conn = get_db_connection()
        if not conn:
            raise HTTPException(status_code=500, detail="Database connection failed")

        try:
            cursor = conn.cursor(dictionary=True)

            # Verificar se lead já existe
            cursor.execute("SELECT user_id FROM users WHERE email = %s", (data.email,))
            existing = cursor.fetchone()

            if existing:
                lead_id = existing["user_id"]
                logger.info(f"Lead existente: {lead_id}")
            else:
                # Criar novo usuário como lead
                cursor.execute("""
                    INSERT INTO users (username, email, phone_number, profession, role, account_status)
                    VALUES (%s, %s, %s, %s, 'lead', 'lead')
                """, (data.nome, data.email, data.telefone, data.profissao or 'Não informado'))

                lead_id = cursor.lastrowid

                # Criar estado CRM
                import json
                notes = json.dumps({
                    "elementor_data": {
                        "source": "elementor",
                        "form_name": data.form_name,
                        "profissao": data.profissao,
                        "utm": {
                            "source": data.utm_source,
                            "medium": data.utm_medium,
                            "campaign": data.utm_campaign,
                            "content": data.utm_content,
                            "term": data.utm_term
                        },
                        "ip_address": data.ip_address,
                        "landing_page_url": data.landing_page_url,
                        "captured_at": data.captured_at
                    }
                }, ensure_ascii=False)

                cursor.execute("""
                    INSERT INTO crm_lead_state (lead_id, current_state, owner_team, notes)
                    VALUES (%s, 'novo', 'marketing', %s)
                """, (lead_id, notes))

                conn.commit()
                logger.info(f"✅ Novo lead criado: {lead_id}")

            cursor.close()
            conn.close()

        except Exception as e:
            if conn:
                conn.close()
            raise e

        if not lead_id:
            raise HTTPException(status_code=500, detail="Falha ao capturar lead")

        # 2. Disparar processamento em background
        background_tasks.add_task(process_lead_background, lead_id)

        logger.info(f"✅ Lead {lead_id} capturado. Processamento iniciado em background.")

        return {
            "success": True,
            "lead_id": lead_id,
            "message": "Lead capturado com sucesso. Processamento automático iniciado.",
            "is_new_user": capture_result.get("is_new_user", False)
        }

    except Exception as e:
        logger.error(f"❌ Erro no webhook Elementor: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/typeform")
async def typeform_webhook(
    data: TypeformWebhook,
    background_tasks: BackgroundTasks
):
    """
    Recebe webhook do Typeform (pesquisa de diagnóstico).

    Atualiza lead existente com respostas do formulário.
    """
    try:
        from mcp_tools.nanda_crm_server import (
            capture_lead_from_typeform as capture_typeform_tool
        )

        logger.info(f"📋 Typeform recebido: {data.email}")

        result = await capture_typeform_tool({
            "email": data.email,
            "respostas": data.respostas,
            "typeform_response_id": data.typeform_response_id,
            "submitted_at": data.submitted_at
        })

        lead_id = result.get("user_id")

        if lead_id:
            # Reprocessar lead com novos dados
            background_tasks.add_task(process_lead_background, lead_id)

        return {
            "success": True,
            "lead_id": lead_id,
            "message": "Respostas do Typeform processadas"
        }

    except Exception as e:
        logger.error(f"❌ Erro no webhook Typeform: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/meeting-completed")
async def meeting_completed_webhook(
    meeting_id: str,
    lead_id: int,
    background_tasks: BackgroundTasks
):
    """
    Webhook chamado quando uma reunião é concluída.

    Dispara análise da call via crm-calls agent.
    """
    try:
        logger.info(f"📞 Call concluída: lead {lead_id}, meeting {meeting_id}")

        orchestrator = get_orchestrator()
        background_tasks.add_task(
            orchestrator.analyze_call,
            lead_id,
            meeting_id
        )

        return {
            "success": True,
            "message": "Análise de call iniciada"
        }

    except Exception as e:
        logger.error(f"❌ Erro no webhook de reunião: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def webhook_health():
    """Health check para webhooks"""
    return {
        "status": "ok",
        "service": "webhooks",
        "orchestrator_loaded": get_orchestrator() is not None
    }
