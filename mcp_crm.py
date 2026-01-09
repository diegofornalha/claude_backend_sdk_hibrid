#!/usr/bin/env python3
"""
MCP Server para CRM Operacional - Turso Local
Ferramentas para gerenciar leads, tarefas, reuniões e vendas
+ Inteligência via Claude Agent SDK
"""

import os
import sys
import json
import uuid
import asyncio
from datetime import datetime
from typing import Optional, List
import libsql_experimental as libsql
from mcp.server.fastmcp import FastMCP

# Adicionar Claude Agent SDK ao path
SDK_PATH = "/home/diagnostico/diagnostico_nanda/claude-agent-sdk-python/src"
if SDK_PATH not in sys.path:
    sys.path.insert(0, SDK_PATH)

# Configuração do banco
DATABASE_PATH = os.getenv(
    "TURSO_DATABASE_PATH",
    "/home/diagnostico/.turso/databases/crm.db"
)

mcp = FastMCP("crm-crm")


def get_connection():
    """Retorna conexão com o banco Turso Local"""
    conn = libsql.connect(DATABASE_PATH)
    # Habilitar WAL mode para melhor concorrência
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def generate_id(prefix: str = ""):
    """Gera ID único"""
    timestamp = int(datetime.now().timestamp())
    unique = uuid.uuid4().hex[:8]
    return f"{prefix}{timestamp}.{unique}" if prefix else f"{timestamp}.{unique}"


# ==================== LEAD STATE ====================

@mcp.tool()
def get_lead_state(lead_id: int) -> dict:
    """
    Obtém o estado atual de um lead no CRM.

    Args:
        lead_id: ID do lead (user_id)

    Returns:
        Estado atual do lead incluindo: current_state, owner_team, sla_due_at, notes
    """
    conn = get_connection()
    cursor = conn.execute(
        "SELECT * FROM crm_lead_state WHERE lead_id = ?",
        (lead_id,)
    )
    row = cursor.fetchone()

    if not row:
        return {"error": f"Lead {lead_id} não encontrado no CRM"}

    columns = [desc[0] for desc in cursor.description]
    return dict(zip(columns, row))


@mcp.tool()
def update_lead_state(
    lead_id: int,
    new_state: str,
    owner_team: Optional[str] = None,
    owner_user_id: Optional[int] = None,
    sla_due_at: Optional[str] = None,
    notes: Optional[str] = None
) -> dict:
    """
    Atualiza o estado de um lead no funil do CRM.

    Args:
        lead_id: ID do lead
        new_state: Novo estado (novo, diagnostico_pendente, diagnostico_agendado,
                   em_atendimento, proposta_enviada, negociacao, produto_vendido,
                   followup_ativo, perdido, reativacao)
        owner_team: Time responsável (marketing, vendas, suporte)
        owner_user_id: ID do usuário responsável
        sla_due_at: Data limite do SLA (ISO 8601)
        notes: Observações

    Returns:
        Confirmação da atualização e evento registrado
    """
    conn = get_connection()

    # Verificar se lead existe
    cursor = conn.execute(
        "SELECT current_state FROM crm_lead_state WHERE lead_id = ?",
        (lead_id,)
    )
    existing = cursor.fetchone()
    old_state = existing[0] if existing else None

    now = datetime.now().isoformat()

    if existing:
        # Update
        conn.execute("""
            UPDATE crm_lead_state
            SET current_state = ?,
                state_updated_at = ?,
                owner_team = COALESCE(?, owner_team),
                owner_user_id = COALESCE(?, owner_user_id),
                sla_due_at = COALESCE(?, sla_due_at),
                notes = COALESCE(?, notes)
            WHERE lead_id = ?
        """, (new_state, now, owner_team, owner_user_id, sla_due_at, notes, lead_id))
    else:
        # Insert
        conn.execute("""
            INSERT INTO crm_lead_state
            (lead_id, current_state, state_updated_at, owner_team, owner_user_id, sla_due_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (lead_id, new_state, now, owner_team, owner_user_id, sla_due_at, notes))

    # Registrar evento
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, channel, payload)
        VALUES (?, ?, 'state_changed', 'system', 'crm', ?)
    """, (event_id, lead_id, json.dumps({
        "old_state": old_state,
        "new_state": new_state,
        "owner_team": owner_team
    })))

    conn.commit()

    return {
        "success": True,
        "lead_id": lead_id,
        "old_state": old_state,
        "new_state": new_state,
        "event_id": event_id
    }


@mcp.tool()
def list_leads_by_state(state: str, limit: int = 50) -> dict:
    """
    Lista leads por estado no funil.

    Args:
        state: Estado a filtrar (novo, diagnostico_pendente, etc.)
        limit: Número máximo de resultados

    Returns:
        Lista de leads no estado especificado
    """
    conn = get_connection()
    cursor = conn.execute("""
        SELECT ls.*, u.name, u.email, u.phone
        FROM crm_lead_state ls
        LEFT JOIN users u ON ls.lead_id = u.user_id
        WHERE ls.current_state = ?
        ORDER BY ls.state_updated_at DESC
        LIMIT ?
    """, (state, limit))

    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    return {
        "state": state,
        "count": len(rows),
        "leads": [dict(zip(columns, row)) for row in rows]
    }


# ==================== LEAD EVENTS ====================

@mcp.tool()
def log_lead_event(
    lead_id: int,
    event_type: str,
    channel: str = "crm",
    actor_type: str = "system",
    actor_id: Optional[int] = None,
    payload: Optional[dict] = None,
    related_meeting_id: Optional[str] = None,
    related_order_id: Optional[str] = None
) -> dict:
    """
    Registra um evento do lead para auditoria e histórico.

    Args:
        lead_id: ID do lead
        event_type: Tipo do evento (lead_captured, diagnostic_scheduled,
                    meeting_scheduled, product_sold, followup_done, lost, etc.)
        channel: Canal do evento (landing_page, whatsapp, telefone, email, crm)
        actor_type: Quem executou (system, ai, human, lead)
        actor_id: ID do ator (se humano)
        payload: Dados adicionais em JSON
        related_meeting_id: ID da reunião relacionada
        related_order_id: ID do pedido relacionado

    Returns:
        Confirmação com event_id
    """
    conn = get_connection()

    event_id = generate_id("evt_")
    payload_json = json.dumps(payload) if payload else None

    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, actor_id, channel,
         payload, related_meeting_id, related_order_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (event_id, lead_id, event_type, actor_type, actor_id, channel,
          payload_json, related_meeting_id, related_order_id))

    conn.commit()

    return {
        "success": True,
        "event_id": event_id,
        "lead_id": lead_id,
        "event_type": event_type
    }


@mcp.tool()
def get_lead_events(lead_id: int, limit: int = 20) -> dict:
    """
    Obtém histórico de eventos de um lead.

    Args:
        lead_id: ID do lead
        limit: Número máximo de eventos

    Returns:
        Lista de eventos em ordem cronológica reversa
    """
    conn = get_connection()
    cursor = conn.execute("""
        SELECT * FROM crm_lead_events
        WHERE lead_id = ?
        ORDER BY event_at DESC
        LIMIT ?
    """, (lead_id, limit))

    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    events = []
    for row in rows:
        event = dict(zip(columns, row))
        if event.get("payload"):
            try:
                event["payload"] = json.loads(event["payload"])
            except:
                pass
        events.append(event)

    return {
        "lead_id": lead_id,
        "count": len(events),
        "events": events
    }


# ==================== TASKS ====================

@mcp.tool()
def create_task(
    lead_id: int,
    task_type: str,
    due_at: Optional[str] = None,
    priority: str = "medium",
    assigned_to_user_id: Optional[int] = None,
    ai_recommendation: Optional[str] = None,
    notes: Optional[str] = None
) -> dict:
    """
    Cria uma tarefa para um lead.

    Args:
        lead_id: ID do lead
        task_type: Tipo (ligar, enviar_proposta, agendar_reuniao, followup,
                   enviar_material, cobrar_pagamento)
        due_at: Data limite (ISO 8601)
        priority: Prioridade (low, medium, high, urgent)
        assigned_to_user_id: ID do usuário responsável
        ai_recommendation: Recomendação da IA
        notes: Observações

    Returns:
        Tarefa criada com task_id
    """
    conn = get_connection()

    task_id = generate_id("task_")

    conn.execute("""
        INSERT INTO crm_tasks
        (task_id, lead_id, task_type, due_at, priority, assigned_to_user_id,
         ai_recommendation, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (task_id, lead_id, task_type, due_at, priority, assigned_to_user_id,
          ai_recommendation, notes))

    # Registrar evento
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, channel, payload)
        VALUES (?, ?, 'task_created', 'ai', 'crm', ?)
    """, (event_id, lead_id, json.dumps({
        "task_id": task_id,
        "task_type": task_type,
        "priority": priority
    })))

    conn.commit()

    return {
        "success": True,
        "task_id": task_id,
        "lead_id": lead_id,
        "task_type": task_type,
        "priority": priority
    }


@mcp.tool()
def complete_task(task_id: str, notes: Optional[str] = None) -> dict:
    """
    Marca uma tarefa como concluída.

    Args:
        task_id: ID da tarefa
        notes: Observações de conclusão

    Returns:
        Confirmação da conclusão
    """
    conn = get_connection()

    # Obter lead_id da tarefa
    cursor = conn.execute(
        "SELECT lead_id, task_type FROM crm_tasks WHERE task_id = ?",
        (task_id,)
    )
    row = cursor.fetchone()

    if not row:
        return {"error": f"Tarefa {task_id} não encontrada"}

    lead_id, task_type = row
    now = datetime.now().isoformat()

    conn.execute("""
        UPDATE crm_tasks
        SET status = 'completed', completed_at = ?, notes = COALESCE(?, notes)
        WHERE task_id = ?
    """, (now, notes, task_id))

    # Registrar evento
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, channel, payload)
        VALUES (?, ?, 'task_completed', 'human', 'crm', ?)
    """, (event_id, lead_id, json.dumps({
        "task_id": task_id,
        "task_type": task_type
    })))

    conn.commit()

    return {
        "success": True,
        "task_id": task_id,
        "status": "completed"
    }


@mcp.tool()
def list_tasks(
    status: str = "open",
    lead_id: Optional[int] = None,
    assigned_to: Optional[int] = None,
    limit: int = 50
) -> dict:
    """
    Lista tarefas com filtros opcionais.

    Args:
        status: Status (open, completed, all)
        lead_id: Filtrar por lead específico
        assigned_to: Filtrar por usuário responsável
        limit: Número máximo de resultados

    Returns:
        Lista de tarefas
    """
    conn = get_connection()

    query = """
        SELECT t.*, u.name as lead_name, u.email as lead_email
        FROM crm_tasks t
        LEFT JOIN users u ON t.lead_id = u.user_id
        WHERE 1=1
    """
    params = []

    if status != "all":
        query += " AND t.status = ?"
        params.append(status)

    if lead_id:
        query += " AND t.lead_id = ?"
        params.append(lead_id)

    if assigned_to:
        query += " AND t.assigned_to_user_id = ?"
        params.append(assigned_to)

    query += " ORDER BY t.due_at ASC, t.priority DESC LIMIT ?"
    params.append(limit)

    cursor = conn.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    return {
        "status_filter": status,
        "count": len(rows),
        "tasks": [dict(zip(columns, row)) for row in rows]
    }


# ==================== MEETINGS ====================

@mcp.tool()
def schedule_meeting(
    lead_id: int,
    meeting_type: str,
    scheduled_at: str,
    title: Optional[str] = None,
    duration_minutes: int = 30,
    google_meet_url: Optional[str] = None,
    google_calendar_event_id: Optional[str] = None,
    notes: Optional[str] = None
) -> dict:
    """
    Agenda uma reunião com um lead.

    Args:
        lead_id: ID do lead
        meeting_type: Tipo (diagnostico, apresentacao, negociacao, followup, suporte)
        scheduled_at: Data/hora agendada (ISO 8601)
        title: Título da reunião
        duration_minutes: Duração em minutos (default 30)
        google_meet_url: URL do Google Meet
        google_calendar_event_id: ID do evento no Google Calendar
        notes: Observações

    Returns:
        Reunião criada com meeting_id
    """
    conn = get_connection()

    meeting_id = generate_id("meet_")

    conn.execute("""
        INSERT INTO crm_meetings
        (meeting_id, lead_id, meeting_type, title, scheduled_at, duration_minutes,
         google_meet_url, google_calendar_event_id, notes, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ai')
    """, (meeting_id, lead_id, meeting_type, title, scheduled_at, duration_minutes,
          google_meet_url, google_calendar_event_id, notes))

    # Registrar evento
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, channel, payload, related_meeting_id)
        VALUES (?, ?, 'meeting_scheduled', 'ai', 'crm', ?, ?)
    """, (event_id, lead_id, json.dumps({
        "meeting_type": meeting_type,
        "scheduled_at": scheduled_at
    }), meeting_id))

    # Atualizar estado do lead se for diagnóstico
    if meeting_type == "diagnostico":
        conn.execute("""
            UPDATE crm_lead_state
            SET current_state = 'diagnostico_agendado', state_updated_at = ?
            WHERE lead_id = ?
        """, (datetime.now().isoformat(), lead_id))

    conn.commit()

    return {
        "success": True,
        "meeting_id": meeting_id,
        "lead_id": lead_id,
        "meeting_type": meeting_type,
        "scheduled_at": scheduled_at
    }


@mcp.tool()
def update_meeting_status(
    meeting_id: str,
    status: str,
    recording_url: Optional[str] = None,
    notes: Optional[str] = None
) -> dict:
    """
    Atualiza o status de uma reunião.

    Args:
        meeting_id: ID da reunião
        status: Novo status (scheduled, confirmed, in_progress, completed, cancelled, no_show)
        recording_url: URL da gravação (se completed)
        notes: Observações

    Returns:
        Confirmação da atualização
    """
    conn = get_connection()

    # Obter dados da reunião
    cursor = conn.execute(
        "SELECT lead_id, meeting_type FROM crm_meetings WHERE meeting_id = ?",
        (meeting_id,)
    )
    row = cursor.fetchone()

    if not row:
        return {"error": f"Reunião {meeting_id} não encontrada"}

    lead_id, meeting_type = row

    conn.execute("""
        UPDATE crm_meetings
        SET status = ?, recording_url = COALESCE(?, recording_url),
            notes = COALESCE(?, notes)
        WHERE meeting_id = ?
    """, (status, recording_url, notes, meeting_id))

    # Registrar evento
    event_type = f"meeting_{status}"
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, channel, payload, related_meeting_id)
        VALUES (?, ?, ?, 'human', 'crm', ?, ?)
    """, (event_id, lead_id, event_type, json.dumps({
        "status": status,
        "meeting_type": meeting_type
    }), meeting_id))

    conn.commit()

    return {
        "success": True,
        "meeting_id": meeting_id,
        "status": status
    }


@mcp.tool()
def list_meetings(
    status: Optional[str] = None,
    lead_id: Optional[int] = None,
    meeting_type: Optional[str] = None,
    limit: int = 50
) -> dict:
    """
    Lista reuniões agendadas.

    Args:
        status: Filtrar por status
        lead_id: Filtrar por lead
        meeting_type: Filtrar por tipo
        limit: Número máximo de resultados

    Returns:
        Lista de reuniões
    """
    conn = get_connection()

    query = """
        SELECT m.*, u.name as lead_name, u.email as lead_email
        FROM crm_meetings m
        LEFT JOIN users u ON m.lead_id = u.user_id
        WHERE 1=1
    """
    params = []

    if status:
        query += " AND m.status = ?"
        params.append(status)

    if lead_id:
        query += " AND m.lead_id = ?"
        params.append(lead_id)

    if meeting_type:
        query += " AND m.meeting_type = ?"
        params.append(meeting_type)

    query += " ORDER BY m.scheduled_at ASC LIMIT ?"
    params.append(limit)

    cursor = conn.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    return {
        "count": len(rows),
        "meetings": [dict(zip(columns, row)) for row in rows]
    }


# ==================== PRODUCTS ====================

@mcp.tool()
def list_products(active_only: bool = True) -> dict:
    """
    Lista produtos disponíveis no catálogo.

    Args:
        active_only: Se True, retorna apenas produtos ativos

    Returns:
        Lista de produtos
    """
    conn = get_connection()

    query = "SELECT * FROM crm_products"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY price ASC"

    cursor = conn.execute(query)
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    return {
        "count": len(rows),
        "products": [dict(zip(columns, row)) for row in rows]
    }


@mcp.tool()
def create_product(
    name: str,
    price: float,
    type: str,
    description: Optional[str] = None,
    recurrence: str = "one_time",
    currency: str = "BRL"
) -> dict:
    """
    Cria um novo produto no catálogo.

    Args:
        name: Nome do produto
        price: Preço
        type: Tipo (consultoria, mentoria, curso, pacote, assinatura)
        description: Descrição
        recurrence: Recorrência (one_time, monthly, quarterly, yearly)
        currency: Moeda (default BRL)

    Returns:
        Produto criado
    """
    conn = get_connection()

    product_id = generate_id("prod_")

    conn.execute("""
        INSERT INTO crm_products
        (product_id, name, description, type, price, currency, recurrence)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (product_id, name, description, type, price, currency, recurrence))

    conn.commit()

    return {
        "success": True,
        "product_id": product_id,
        "name": name,
        "price": price
    }


# ==================== ORDERS ====================

@mcp.tool()
def create_order(
    lead_id: int,
    product_id: str,
    amount: float,
    seller_user_id: Optional[int] = None,
    discount_percent: float = 0,
    payment_method: Optional[str] = None,
    notes: Optional[str] = None
) -> dict:
    """
    Cria um pedido/venda para um lead.

    Args:
        lead_id: ID do lead
        product_id: ID do produto
        amount: Valor bruto
        seller_user_id: ID do vendedor
        discount_percent: Desconto em % (0-100)
        payment_method: Método (pix, cartao, boleto, transferencia)
        notes: Observações

    Returns:
        Pedido criado
    """
    conn = get_connection()

    order_id = generate_id("order_")
    final_amount = amount * (1 - discount_percent / 100)

    conn.execute("""
        INSERT INTO crm_orders
        (order_id, lead_id, product_id, amount, discount_percent, final_amount,
         seller_user_id, payment_method, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (order_id, lead_id, product_id, amount, discount_percent, final_amount,
          seller_user_id, payment_method, notes))

    # Registrar evento
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, channel, payload, related_order_id)
        VALUES (?, ?, 'order_created', 'human', 'crm', ?, ?)
    """, (event_id, lead_id, json.dumps({
        "product_id": product_id,
        "amount": amount,
        "final_amount": final_amount
    }), order_id))

    conn.commit()

    return {
        "success": True,
        "order_id": order_id,
        "lead_id": lead_id,
        "amount": amount,
        "final_amount": final_amount
    }


@mcp.tool()
def update_order_status(
    order_id: str,
    status: str,
    payment_status: Optional[str] = None,
    loss_reason: Optional[str] = None,
    notes: Optional[str] = None
) -> dict:
    """
    Atualiza o status de um pedido.

    Args:
        order_id: ID do pedido
        status: Novo status (pending, approved, completed, cancelled, refunded)
        payment_status: Status do pagamento (pending, paid, failed)
        loss_reason: Motivo da perda (se cancelled)
        notes: Observações

    Returns:
        Confirmação da atualização
    """
    conn = get_connection()

    # Obter dados do pedido
    cursor = conn.execute(
        "SELECT lead_id, product_id FROM crm_orders WHERE order_id = ?",
        (order_id,)
    )
    row = cursor.fetchone()

    if not row:
        return {"error": f"Pedido {order_id} não encontrado"}

    lead_id, product_id = row
    now = datetime.now().isoformat()

    # Update order
    paid_at = now if payment_status == "paid" else None
    conn.execute("""
        UPDATE crm_orders
        SET status = ?,
            payment_status = COALESCE(?, payment_status),
            paid_at = COALESCE(?, paid_at),
            loss_reason = ?,
            notes = COALESCE(?, notes)
        WHERE order_id = ?
    """, (status, payment_status, paid_at, loss_reason, notes, order_id))

    # Registrar evento
    event_type = "product_sold" if status == "completed" else f"order_{status}"
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, channel, payload, related_order_id)
        VALUES (?, ?, ?, 'human', 'crm', ?, ?)
    """, (event_id, lead_id, event_type, json.dumps({
        "status": status,
        "payment_status": payment_status,
        "loss_reason": loss_reason
    }), order_id))

    # Atualizar estado do lead
    if status == "completed":
        conn.execute("""
            UPDATE crm_lead_state
            SET current_state = 'produto_vendido', state_updated_at = ?
            WHERE lead_id = ?
        """, (now, lead_id))
    elif status == "cancelled" and loss_reason:
        conn.execute("""
            UPDATE crm_lead_state
            SET current_state = 'perdido', state_updated_at = ?, notes = ?
            WHERE lead_id = ?
        """, (now, loss_reason, lead_id))

    conn.commit()

    return {
        "success": True,
        "order_id": order_id,
        "status": status
    }


@mcp.tool()
def list_orders(
    status: Optional[str] = None,
    lead_id: Optional[int] = None,
    limit: int = 50
) -> dict:
    """
    Lista pedidos/vendas.

    Args:
        status: Filtrar por status
        lead_id: Filtrar por lead
        limit: Número máximo de resultados

    Returns:
        Lista de pedidos
    """
    conn = get_connection()

    query = """
        SELECT o.*, u.name as lead_name, u.email as lead_email,
               p.name as product_name
        FROM crm_orders o
        LEFT JOIN users u ON o.lead_id = u.user_id
        LEFT JOIN crm_products p ON o.product_id = p.product_id
        WHERE 1=1
    """
    params = []

    if status:
        query += " AND o.status = ?"
        params.append(status)

    if lead_id:
        query += " AND o.lead_id = ?"
        params.append(lead_id)

    query += " ORDER BY o.created_at DESC LIMIT ?"
    params.append(limit)

    cursor = conn.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    return {
        "count": len(rows),
        "orders": [dict(zip(columns, row)) for row in rows]
    }


# ==================== FOLLOWUPS ====================

@mcp.tool()
def create_followup(
    lead_id: int,
    followup_type: str,
    scheduled_at: Optional[str] = None,
    order_id: Optional[str] = None,
    channel: str = "whatsapp",
    message_template: Optional[str] = None,
    notes: Optional[str] = None
) -> dict:
    """
    Cria um followup para um lead (pós-venda ou nutrição).

    Args:
        lead_id: ID do lead
        followup_type: Tipo (pos_venda, onboarding, check_in, renovacao, upsell, reativacao)
        scheduled_at: Data/hora agendada (ISO 8601)
        order_id: ID do pedido relacionado (se pós-venda)
        channel: Canal (whatsapp, email, telefone)
        message_template: Template da mensagem
        notes: Observações

    Returns:
        Followup criado com followup_id
    """
    conn = get_connection()

    followup_id = generate_id("fu_")

    conn.execute("""
        INSERT INTO crm_followups
        (followup_id, lead_id, order_id, followup_type, scheduled_at, channel,
         message_template, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (followup_id, lead_id, order_id, followup_type, scheduled_at, channel,
          message_template, notes))

    # Registrar evento
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, channel, payload, related_order_id)
        VALUES (?, ?, 'followup_scheduled', 'ai', 'crm', ?, ?)
    """, (event_id, lead_id, json.dumps({
        "followup_id": followup_id,
        "followup_type": followup_type,
        "scheduled_at": scheduled_at
    }), order_id))

    # Atualizar estado do lead
    conn.execute("""
        UPDATE crm_lead_state
        SET current_state = 'followup_ativo', state_updated_at = ?
        WHERE lead_id = ?
    """, (datetime.now().isoformat(), lead_id))

    conn.commit()

    return {
        "success": True,
        "followup_id": followup_id,
        "lead_id": lead_id,
        "followup_type": followup_type,
        "scheduled_at": scheduled_at
    }


@mcp.tool()
def complete_followup(
    followup_id: str,
    response: Optional[str] = None,
    create_next: bool = False,
    next_scheduled_at: Optional[str] = None,
    notes: Optional[str] = None
) -> dict:
    """
    Completa um followup e opcionalmente agenda o próximo.

    Args:
        followup_id: ID do followup
        response: Resposta recebida do lead
        create_next: Se deve criar próximo followup
        next_scheduled_at: Data do próximo (se create_next=True)
        notes: Observações

    Returns:
        Confirmação e ID do próximo followup se criado
    """
    conn = get_connection()

    # Obter dados do followup
    cursor = conn.execute(
        "SELECT lead_id, order_id, followup_type, channel FROM crm_followups WHERE followup_id = ?",
        (followup_id,)
    )
    row = cursor.fetchone()

    if not row:
        return {"error": f"Followup {followup_id} não encontrado"}

    lead_id, order_id, followup_type, channel = row
    now = datetime.now().isoformat()

    # Completar followup atual
    conn.execute("""
        UPDATE crm_followups
        SET status = 'completed', completed_at = ?, response = ?, notes = COALESCE(?, notes)
        WHERE followup_id = ?
    """, (now, response, notes, followup_id))

    # Registrar evento
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, channel, payload, related_order_id)
        VALUES (?, ?, 'followup_done', 'human', 'crm', ?, ?)
    """, (event_id, lead_id, json.dumps({
        "followup_id": followup_id,
        "followup_type": followup_type,
        "response": response
    }), order_id))

    result = {
        "success": True,
        "followup_id": followup_id,
        "status": "completed"
    }

    # Criar próximo followup se solicitado
    if create_next and next_scheduled_at:
        next_followup_id = generate_id("fu_")
        conn.execute("""
            INSERT INTO crm_followups
            (followup_id, lead_id, order_id, followup_type, scheduled_at, channel)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (next_followup_id, lead_id, order_id, followup_type, next_scheduled_at, channel))

        # Link próximo followup
        conn.execute("""
            UPDATE crm_followups SET next_followup_id = ? WHERE followup_id = ?
        """, (next_followup_id, followup_id))

        result["next_followup_id"] = next_followup_id
        result["next_scheduled_at"] = next_scheduled_at

    conn.commit()

    return result


@mcp.tool()
def list_followups(
    status: str = "pending",
    lead_id: Optional[int] = None,
    order_id: Optional[str] = None,
    followup_type: Optional[str] = None,
    limit: int = 50
) -> dict:
    """
    Lista followups com filtros.

    Args:
        status: Status (pending, sent, completed, all)
        lead_id: Filtrar por lead
        order_id: Filtrar por pedido
        followup_type: Filtrar por tipo
        limit: Número máximo de resultados

    Returns:
        Lista de followups
    """
    conn = get_connection()

    query = """
        SELECT f.*, u.name as lead_name, u.email as lead_email, u.phone as lead_phone
        FROM crm_followups f
        LEFT JOIN users u ON f.lead_id = u.user_id
        WHERE 1=1
    """
    params = []

    if status != "all":
        query += " AND f.status = ?"
        params.append(status)

    if lead_id:
        query += " AND f.lead_id = ?"
        params.append(lead_id)

    if order_id:
        query += " AND f.order_id = ?"
        params.append(order_id)

    if followup_type:
        query += " AND f.followup_type = ?"
        params.append(followup_type)

    query += " ORDER BY f.scheduled_at ASC LIMIT ?"
    params.append(limit)

    cursor = conn.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    return {
        "status_filter": status,
        "count": len(rows),
        "followups": [dict(zip(columns, row)) for row in rows]
    }


@mcp.tool()
def send_followup(
    followup_id: str,
    message_sent: str
) -> dict:
    """
    Marca um followup como enviado.

    Args:
        followup_id: ID do followup
        message_sent: Mensagem que foi enviada

    Returns:
        Confirmação do envio
    """
    conn = get_connection()

    cursor = conn.execute(
        "SELECT lead_id FROM crm_followups WHERE followup_id = ?",
        (followup_id,)
    )
    row = cursor.fetchone()

    if not row:
        return {"error": f"Followup {followup_id} não encontrado"}

    lead_id = row[0]

    conn.execute("""
        UPDATE crm_followups
        SET status = 'sent', message_sent = ?
        WHERE followup_id = ?
    """, (message_sent, followup_id))

    conn.commit()

    return {
        "success": True,
        "followup_id": followup_id,
        "status": "sent"
    }


# ==================== DIAGNOSIS HUMAN ====================

@mcp.tool()
def save_diagnosis_human(
    lead_id: int,
    meeting_id: Optional[str] = None,
    diagnosed_by_user_id: Optional[int] = None,
    deep_pains: Optional[list] = None,
    real_barriers: Optional[list] = None,
    emotional_profile: Optional[str] = None,
    investment_capacity: Optional[str] = None,
    urgency_level: str = "medium",
    recommended_route: Optional[str] = None,
    route_justification: Optional[str] = None,
    ai_hints_used: Optional[list] = None,
    notes: Optional[str] = None
) -> dict:
    """
    Salva o diagnóstico humano coletado durante a call.

    Args:
        lead_id: ID do lead
        meeting_id: ID da reunião/call onde foi coletado
        diagnosed_by_user_id: ID de quem diagnosticou (Nanda)
        deep_pains: Lista de dores profundas identificadas
        real_barriers: Lista de barreiras reais (tempo, dinheiro, etc)
        emotional_profile: Perfil emocional (ansioso, decidido, inseguro, etc)
        investment_capacity: Capacidade de investimento (baixa, media, alta)
        urgency_level: Nível de urgência (low, medium, high, critical)
        recommended_route: Rota recomendada (vendas, nutricao, perdido, futuro)
        route_justification: Justificativa da rota escolhida
        ai_hints_used: Lista de hints da IA usados durante a call
        notes: Observações adicionais

    Returns:
        Diagnóstico salvo com diagnosis_id
    """
    conn = get_connection()

    diagnosis_id = generate_id("diag_")

    conn.execute("""
        INSERT INTO crm_diagnosis_human
        (diagnosis_id, lead_id, meeting_id, diagnosed_by_user_id,
         deep_pains, real_barriers, emotional_profile, investment_capacity,
         urgency_level, recommended_route, route_justification, ai_hints_used, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        diagnosis_id, lead_id, meeting_id, diagnosed_by_user_id,
        json.dumps(deep_pains) if deep_pains else None,
        json.dumps(real_barriers) if real_barriers else None,
        emotional_profile, investment_capacity, urgency_level,
        recommended_route, route_justification,
        json.dumps(ai_hints_used) if ai_hints_used else None,
        notes
    ))

    # Registrar evento
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, actor_id, channel, payload, related_meeting_id)
        VALUES (?, ?, 'diagnosis_completed', 'human', ?, 'call', ?, ?)
    """, (event_id, lead_id, diagnosed_by_user_id, json.dumps({
        "diagnosis_id": diagnosis_id,
        "urgency_level": urgency_level,
        "recommended_route": recommended_route
    }), meeting_id))

    # Atualizar estado do lead baseado na rota
    state_map = {
        "vendas": "proposta_enviada",
        "nutricao": "em_atendimento",
        "perdido": "perdido",
        "futuro": "reativacao"
    }
    if recommended_route and recommended_route in state_map:
        conn.execute("""
            UPDATE crm_lead_state
            SET current_state = ?, state_updated_at = ?, notes = ?
            WHERE lead_id = ?
        """, (state_map[recommended_route], datetime.now().isoformat(), route_justification, lead_id))

    conn.commit()

    return {
        "success": True,
        "diagnosis_id": diagnosis_id,
        "lead_id": lead_id,
        "recommended_route": recommended_route,
        "urgency_level": urgency_level
    }


@mcp.tool()
def get_diagnosis_human(lead_id: int) -> dict:
    """
    Obtém o diagnóstico humano de um lead.

    Args:
        lead_id: ID do lead

    Returns:
        Diagnóstico completo com dores, barreiras, perfil e rota
    """
    conn = get_connection()
    cursor = conn.execute("""
        SELECT * FROM crm_diagnosis_human
        WHERE lead_id = ?
        ORDER BY diagnosed_at DESC
        LIMIT 1
    """, (lead_id,))

    row = cursor.fetchone()
    if not row:
        return {"error": f"Diagnóstico não encontrado para lead {lead_id}"}

    columns = [desc[0] for desc in cursor.description]
    diagnosis = dict(zip(columns, row))

    # Parse JSON fields
    for field in ['deep_pains', 'real_barriers', 'ai_hints_used']:
        if diagnosis.get(field):
            try:
                diagnosis[field] = json.loads(diagnosis[field])
            except:
                pass

    return diagnosis


@mcp.tool()
def list_diagnoses_by_route(
    recommended_route: str,
    limit: int = 50
) -> dict:
    """
    Lista diagnósticos por rota recomendada.

    Args:
        recommended_route: Rota (vendas, nutricao, perdido, futuro)
        limit: Número máximo de resultados

    Returns:
        Lista de diagnósticos
    """
    conn = get_connection()
    cursor = conn.execute("""
        SELECT d.*, u.name as lead_name, u.email as lead_email
        FROM crm_diagnosis_human d
        LEFT JOIN users u ON d.lead_id = u.user_id
        WHERE d.recommended_route = ?
        ORDER BY d.diagnosed_at DESC
        LIMIT ?
    """, (recommended_route, limit))

    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    return {
        "route": recommended_route,
        "count": len(rows),
        "diagnoses": [dict(zip(columns, row)) for row in rows]
    }


# ==================== CAPTURA DE LEADS (ELEMENTOR/TYPEFORM) ====================

@mcp.tool()
def capture_lead_from_elementor(
    nome: str,
    email: str,
    telefone: Optional[str] = None,
    profissao: Optional[str] = None,
    utm_source: Optional[str] = None,
    utm_medium: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    utm_content: Optional[str] = None,
    utm_term: Optional[str] = None,
    ip_address: Optional[str] = None,
    landing_page_url: Optional[str] = None,
    form_name: Optional[str] = None,
    captured_at: Optional[str] = None
) -> dict:
    """
    Captura lead vindo do Elementor Forms (primeiro ponto de contato).
    Cria usuário se não existir e registra entrada no CRM.

    Args:
        nome: Nome completo do lead
        email: Email do lead (usado para verificar duplicidade)
        telefone: Telefone/WhatsApp
        profissao: Profissão informada
        utm_source: Origem (facebook, google, instagram)
        utm_medium: Meio (cpc, organic, social)
        utm_campaign: Nome da campanha
        utm_content: Conteúdo/criativo
        utm_term: Termo de busca ou ID do anúncio
        ip_address: IP do lead
        landing_page_url: URL da página de captura
        form_name: Nome do formulário (ex: Webinar, Landing Page, Diagnóstico)
        captured_at: Data/hora da captura (ISO 8601)

    Returns:
        Lead criado com user_id e status inicial
    """
    conn = get_connection()
    now = datetime.now().isoformat()
    captured_at = captured_at or now

    # Verificar se email já existe
    cursor = conn.execute(
        "SELECT user_id, username FROM users WHERE email = ?",
        (email.lower().strip(),)
    )
    existing = cursor.fetchone()

    if existing:
        user_id = existing[0]
        is_new = False

        # Verificar se já está no CRM
        cursor = conn.execute(
            "SELECT current_state FROM crm_lead_state WHERE lead_id = ?",
            (user_id,)
        )
        crm_state = cursor.fetchone()
    else:
        # Criar novo usuário (lead)
        # password_hash placeholder para leads - não fazem login
        import hashlib
        password_placeholder = hashlib.sha256(f"lead_{email}".encode()).hexdigest()

        cursor = conn.execute("""
            INSERT INTO users (username, email, phone_number, password_hash, registration_date, profession, account_status, role)
            VALUES (?, ?, ?, ?, ?, ?, 'lead', 'lead')
        """, (nome.strip(), email.lower().strip(), telefone, password_placeholder, captured_at, profissao or 'Não informado'))
        conn.commit()

        # Obter ID gerado
        cursor = conn.execute(
            "SELECT user_id FROM users WHERE email = ?",
            (email.lower().strip(),)
        )
        user_id = cursor.fetchone()[0]
        is_new = True
        crm_state = None

    # Montar payload com dados do Elementor
    elementor_payload = {
        "source": "elementor",
        "form_name": form_name,
        "profissao": profissao,
        "utm": {
            "source": utm_source,
            "medium": utm_medium,
            "campaign": utm_campaign,
            "content": utm_content,
            "term": utm_term
        },
        "ip_address": ip_address,
        "landing_page_url": landing_page_url,
        "captured_at": captured_at
    }

    # Criar/Atualizar entrada no CRM
    if not crm_state:
        # Novo no CRM
        conn.execute("""
            INSERT INTO crm_lead_state
            (lead_id, current_state, state_updated_at, owner_team, notes)
            VALUES (?, 'novo', ?, 'marketing', ?)
        """, (user_id, now, json.dumps({"elementor_data": elementor_payload})))
        current_state = "novo"
    else:
        # Já existe no CRM, apenas atualizar notas
        cursor = conn.execute(
            "SELECT notes FROM crm_lead_state WHERE lead_id = ?",
            (user_id,)
        )
        existing_notes = cursor.fetchone()[0] or "{}"
        try:
            notes_data = json.loads(existing_notes) if existing_notes.startswith('{') else {"manual": existing_notes}
        except:
            notes_data = {"manual": existing_notes}

        notes_data["elementor_data"] = elementor_payload

        conn.execute("""
            UPDATE crm_lead_state SET notes = ? WHERE lead_id = ?
        """, (json.dumps(notes_data), user_id))

        current_state = crm_state[0]

    # Registrar evento de captura
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, channel, payload)
        VALUES (?, ?, 'lead_captured', 'system', 'landing_page', ?)
    """, (event_id, user_id, json.dumps(elementor_payload)))

    conn.commit()

    return {
        "success": True,
        "user_id": user_id,
        "is_new_user": is_new,
        "email": email,
        "nome": nome,
        "current_state": current_state,
        "event_id": event_id,
        "utm_source": utm_source,
        "utm_campaign": utm_campaign,
        "message": f"Lead {'criado' if is_new else 'atualizado'} com sucesso. Aguardando Typeform para diagnóstico."
    }


@mcp.tool()
def capture_lead_from_typeform(
    email: str,
    respostas: dict,
    typeform_response_id: Optional[str] = None,
    submitted_at: Optional[str] = None
) -> dict:
    """
    Captura respostas do Typeform (pesquisa de diagnóstico).
    Atualiza lead existente ou cria se não existir.

    Args:
        email: Email do lead (para vincular com Elementor)
        respostas: Dicionário com todas as respostas do Typeform
        typeform_response_id: ID da resposta no Typeform
        submitted_at: Data/hora do envio

    Returns:
        Lead atualizado com diagnóstico inicial
    """
    conn = get_connection()
    now = datetime.now().isoformat()
    submitted_at = submitted_at or now

    # Buscar usuário pelo email
    cursor = conn.execute(
        "SELECT user_id, username FROM users WHERE email = ?",
        (email.lower().strip(),)
    )
    user_row = cursor.fetchone()

    if not user_row:
        return {
            "error": f"Lead com email {email} não encontrado. Deveria ter vindo do Elementor primeiro.",
            "suggestion": "Use capture_lead_from_elementor primeiro"
        }

    user_id = user_row[0]
    lead_name = user_row[1]

    # Montar payload do Typeform
    typeform_payload = {
        "source": "typeform",
        "response_id": typeform_response_id,
        "respostas": respostas,
        "submitted_at": submitted_at
    }

    # Atualizar estado para diagnóstico pendente
    cursor = conn.execute(
        "SELECT notes FROM crm_lead_state WHERE lead_id = ?",
        (user_id,)
    )
    notes_row = cursor.fetchone()

    if notes_row:
        existing_notes = notes_row[0] or "{}"
        try:
            notes_data = json.loads(existing_notes) if existing_notes.startswith('{') else {"manual": existing_notes}
        except:
            notes_data = {}
    else:
        notes_data = {}
        # Criar entrada se não existe
        conn.execute("""
            INSERT INTO crm_lead_state (lead_id, current_state, state_updated_at, owner_team)
            VALUES (?, 'diagnostico_pendente', ?, 'vendas')
        """, (user_id, now))

    notes_data["typeform_data"] = typeform_payload

    conn.execute("""
        UPDATE crm_lead_state
        SET current_state = 'diagnostico_pendente',
            state_updated_at = ?,
            owner_team = 'vendas',
            notes = ?
        WHERE lead_id = ?
    """, (now, json.dumps(notes_data), user_id))

    # Registrar evento
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, channel, payload)
        VALUES (?, ?, 'typeform_submitted', 'lead', 'typeform', ?)
    """, (event_id, user_id, json.dumps(typeform_payload)))

    conn.commit()

    return {
        "success": True,
        "user_id": user_id,
        "nome": lead_name,
        "email": email,
        "current_state": "diagnostico_pendente",
        "event_id": event_id,
        "message": f"Typeform recebido! Lead {lead_name} pronto para agendamento de diagnóstico."
    }


@mcp.tool()
def get_lead_by_email(email: str) -> dict:
    """
    Busca lead pelo email (útil para verificar se já existe).

    Args:
        email: Email do lead

    Returns:
        Dados do lead se existir
    """
    conn = get_connection()

    cursor = conn.execute("""
        SELECT u.user_id, u.username, u.email, u.phone_number, u.registration_date, u.profession,
               ls.current_state, ls.owner_team, ls.state_updated_at, ls.notes
        FROM users u
        LEFT JOIN crm_lead_state ls ON u.user_id = ls.lead_id
        WHERE u.email = ?
    """, (email.lower().strip(),))

    row = cursor.fetchone()

    if not row:
        return {"found": False, "email": email}

    columns = ['user_id', 'name', 'email', 'phone', 'created_at', 'profession',
               'current_state', 'owner_team', 'state_updated_at', 'notes']
    lead = dict(zip(columns, row))

    # Parse notes
    if lead.get('notes'):
        try:
            lead['notes_parsed'] = json.loads(lead['notes'])
        except:
            pass

    lead['found'] = True
    return lead


# ==================== INTEGRAÇÃO DATA LAKE ====================
# Preparado para receber dados do outro dev quando Data Lake estiver pronto

@mcp.tool()
def update_lead_intelligence(
    lead_id: int,
    lead_score: Optional[float] = None,
    lead_temperature: Optional[str] = None,
    cluster_id: Optional[str] = None,
    cluster_name: Optional[str] = None,
    persona: Optional[str] = None,
    enrichment_data: Optional[dict] = None,
    call_intelligence: Optional[dict] = None,
    source: str = "datalake"
) -> dict:
    """
    Recebe dados de inteligência do Data Lake e atualiza o lead.
    Usado pelo outro dev para sincronizar scores, clusters e análises.

    Args:
        lead_id: ID do lead
        lead_score: Score calculado (0-100)
        lead_temperature: Temperatura (cold, warm, hot, burning)
        cluster_id: ID do cluster
        cluster_name: Nome do cluster/segmento
        persona: Persona identificada
        enrichment_data: Dados de enriquecimento (Instagram, etc) - JSON
        call_intelligence: Análise da call pela IA - JSON
        source: Origem dos dados (datalake, manual)

    Returns:
        Confirmação da atualização
    """
    conn = get_connection()

    # Verificar se lead existe no CRM
    cursor = conn.execute(
        "SELECT lead_id FROM crm_lead_state WHERE lead_id = ?",
        (lead_id,)
    )
    if not cursor.fetchone():
        # Criar entrada se não existe
        conn.execute("""
            INSERT INTO crm_lead_state (lead_id, current_state)
            VALUES (?, 'novo')
        """, (lead_id,))

    # Construir payload de inteligência
    intelligence = {
        "lead_score": lead_score,
        "lead_temperature": lead_temperature,
        "cluster_id": cluster_id,
        "cluster_name": cluster_name,
        "persona": persona,
        "enrichment_data": enrichment_data,
        "call_intelligence": call_intelligence,
        "updated_at": datetime.now().isoformat()
    }

    # Atualizar notes com inteligência (merge com existente)
    cursor = conn.execute(
        "SELECT notes FROM crm_lead_state WHERE lead_id = ?",
        (lead_id,)
    )
    row = cursor.fetchone()
    existing_notes = row[0] if row and row[0] else "{}"

    try:
        notes_data = json.loads(existing_notes) if existing_notes.startswith('{') else {"manual_notes": existing_notes}
    except:
        notes_data = {"manual_notes": existing_notes}

    notes_data["datalake_intelligence"] = intelligence

    conn.execute("""
        UPDATE crm_lead_state
        SET notes = ?, state_updated_at = ?
        WHERE lead_id = ?
    """, (json.dumps(notes_data), datetime.now().isoformat(), lead_id))

    # Registrar evento
    event_id = generate_id("evt_")
    conn.execute("""
        INSERT INTO crm_lead_events
        (event_id, lead_id, event_type, actor_type, channel, payload)
        VALUES (?, ?, 'intelligence_updated', 'system', ?, ?)
    """, (event_id, lead_id, source, json.dumps(intelligence)))

    conn.commit()

    return {
        "success": True,
        "lead_id": lead_id,
        "lead_score": lead_score,
        "lead_temperature": lead_temperature,
        "cluster_name": cluster_name,
        "source": source
    }


@mcp.tool()
def get_lead_full_context(lead_id: int) -> dict:
    """
    Obtém contexto completo do lead para preparar call.
    Combina: estado CRM + diagnóstico humano + inteligência do Data Lake

    Args:
        lead_id: ID do lead

    Returns:
        Contexto completo para briefing da call
    """
    conn = get_connection()
    result = {"lead_id": lead_id}

    # 1. Dados do usuário
    cursor = conn.execute("""
        SELECT user_id, name, email, phone, created_at
        FROM users WHERE user_id = ?
    """, (lead_id,))
    row = cursor.fetchone()
    if row:
        columns = [desc[0] for desc in cursor.description]
        result["user"] = dict(zip(columns, row))

    # 2. Estado no CRM
    cursor = conn.execute("""
        SELECT * FROM crm_lead_state WHERE lead_id = ?
    """, (lead_id,))
    row = cursor.fetchone()
    if row:
        columns = [desc[0] for desc in cursor.description]
        state = dict(zip(columns, row))
        # Parse notes para extrair inteligência
        if state.get("notes"):
            try:
                notes_data = json.loads(state["notes"])
                state["notes_parsed"] = notes_data
                if "datalake_intelligence" in notes_data:
                    result["datalake_intelligence"] = notes_data["datalake_intelligence"]
            except:
                pass
        result["crm_state"] = state

    # 3. Diagnóstico humano
    cursor = conn.execute("""
        SELECT * FROM crm_diagnosis_human
        WHERE lead_id = ?
        ORDER BY diagnosed_at DESC LIMIT 1
    """, (lead_id,))
    row = cursor.fetchone()
    if row:
        columns = [desc[0] for desc in cursor.description]
        diagnosis = dict(zip(columns, row))
        for field in ['deep_pains', 'real_barriers', 'ai_hints_used']:
            if diagnosis.get(field):
                try:
                    diagnosis[field] = json.loads(diagnosis[field])
                except:
                    pass
        result["diagnosis_human"] = diagnosis

    # 4. Reuniões agendadas
    cursor = conn.execute("""
        SELECT * FROM crm_meetings
        WHERE lead_id = ? AND status IN ('scheduled', 'confirmed')
        ORDER BY scheduled_at ASC
    """, (lead_id,))
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    result["upcoming_meetings"] = [dict(zip(columns, row)) for row in rows]

    # 5. Tarefas pendentes
    cursor = conn.execute("""
        SELECT * FROM crm_tasks
        WHERE lead_id = ? AND status = 'open'
        ORDER BY due_at ASC
    """, (lead_id,))
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    result["open_tasks"] = [dict(zip(columns, row)) for row in rows]

    # 6. Pedidos/Vendas
    cursor = conn.execute("""
        SELECT o.*, p.name as product_name
        FROM crm_orders o
        LEFT JOIN crm_products p ON o.product_id = p.product_id
        WHERE o.lead_id = ?
        ORDER BY o.created_at DESC
    """, (lead_id,))
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    result["orders"] = [dict(zip(columns, row)) for row in rows]

    # 7. Últimos eventos
    cursor = conn.execute("""
        SELECT * FROM crm_lead_events
        WHERE lead_id = ?
        ORDER BY event_at DESC LIMIT 10
    """, (lead_id,))
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    events = []
    for row in rows:
        event = dict(zip(columns, row))
        if event.get("payload"):
            try:
                event["payload"] = json.loads(event["payload"])
            except:
                pass
        events.append(event)
    result["recent_events"] = events

    return result


# ==================== IA COM CLAUDE AGENT SDK ====================

def _run_async(coro):
    """Helper para rodar coroutine em contexto sync"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Já tem um loop rodando, criar task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=60)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


async def _query_claude(prompt: str, system_prompt: str) -> str:
    """Query Claude Agent SDK"""
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            max_turns=1,
        )

        result_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        result_text += block.text

        return result_text
    except Exception as e:
        return f"Erro ao consultar IA: {str(e)}"


@mcp.tool()
def generate_lead_briefing(lead_id: int) -> dict:
    """
    Gera briefing inteligente para preparar call com lead.
    Usa Claude Agent SDK para analisar dados e gerar insights.

    Args:
        lead_id: ID do lead

    Returns:
        Briefing com talking points, objeções prováveis, perguntas sugeridas
    """
    # Buscar contexto completo do lead
    conn = get_connection()

    # Dados do usuário
    cursor = conn.execute("""
        SELECT name, email, phone, created_at FROM users WHERE user_id = ?
    """, (lead_id,))
    user_row = cursor.fetchone()

    if not user_row:
        return {"error": f"Lead {lead_id} não encontrado"}

    user_data = {
        "name": user_row[0],
        "email": user_row[1],
        "phone": user_row[2],
        "created_at": user_row[3]
    }

    # Estado no CRM
    cursor = conn.execute("""
        SELECT current_state, notes FROM crm_lead_state WHERE lead_id = ?
    """, (lead_id,))
    state_row = cursor.fetchone()
    state_data = {"current_state": state_row[0] if state_row else "novo", "notes": state_row[1] if state_row else None}

    # Diagnóstico anterior (se houver)
    cursor = conn.execute("""
        SELECT deep_pains, real_barriers, emotional_profile, investment_capacity, urgency_level
        FROM crm_diagnosis_human WHERE lead_id = ? ORDER BY diagnosed_at DESC LIMIT 1
    """, (lead_id,))
    diag_row = cursor.fetchone()
    diagnosis_data = None
    if diag_row:
        diagnosis_data = {
            "deep_pains": diag_row[0],
            "real_barriers": diag_row[1],
            "emotional_profile": diag_row[2],
            "investment_capacity": diag_row[3],
            "urgency_level": diag_row[4]
        }

    # Histórico de eventos
    cursor = conn.execute("""
        SELECT event_type, event_at, payload FROM crm_lead_events
        WHERE lead_id = ? ORDER BY event_at DESC LIMIT 5
    """, (lead_id,))
    events = [{"type": r[0], "at": r[1], "payload": r[2]} for r in cursor.fetchall()]

    # Construir prompt para Claude
    context = f"""
Lead: {user_data['name']}
Email: {user_data['email']}
Estado atual: {state_data['current_state']}
Cadastrado em: {user_data['created_at']}
"""

    if diagnosis_data:
        context += f"""
Diagnóstico anterior:
- Dores: {diagnosis_data['deep_pains']}
- Barreiras: {diagnosis_data['real_barriers']}
- Perfil emocional: {diagnosis_data['emotional_profile']}
- Capacidade investimento: {diagnosis_data['investment_capacity']}
- Urgência: {diagnosis_data['urgency_level']}
"""

    if events:
        context += "\nÚltimos eventos:\n"
        for e in events:
            context += f"- {e['type']} em {e['at']}\n"

    prompt = f"""Analise este lead e gere um briefing para a próxima call:

{context}

Responda em JSON com esta estrutura:
{{
    "resumo": "breve resumo do lead",
    "talking_points": ["ponto 1", "ponto 2", "ponto 3"],
    "objecoes_provaveis": ["objeção 1", "objeção 2"],
    "perguntas_sugeridas": ["pergunta 1", "pergunta 2", "pergunta 3"],
    "gatilhos_compra": ["gatilho 1", "gatilho 2"],
    "score_estimado": 0-100,
    "temperatura": "cold|warm|hot|burning"
}}
"""

    system_prompt = """Você é um especialista em vendas consultivas para profissionais e empresários.
Analise o lead e gere insights práticos para a próxima call.
Responda APENAS com o JSON solicitado, sem texto adicional."""

    # Executar query ao Claude
    async def run_query():
        return await _query_claude(prompt, system_prompt)

    result_text = _run_async(run_query())

    # Tentar parsear JSON
    try:
        # Limpar possíveis markers de código
        clean_text = result_text.strip()
        if clean_text.startswith("```"):
            clean_text = clean_text.split("```")[1]
            if clean_text.startswith("json"):
                clean_text = clean_text[4:]
        briefing = json.loads(clean_text)
    except:
        briefing = {"raw_response": result_text}

    return {
        "lead_id": lead_id,
        "lead_name": user_data['name'],
        "briefing": briefing,
        "generated_at": datetime.now().isoformat()
    }


@mcp.tool()
def analyze_diagnosis_and_suggest_route(
    lead_id: int,
    deep_pains: list,
    real_barriers: list,
    emotional_profile: str,
    investment_capacity: str,
    urgency_level: str
) -> dict:
    """
    Analisa diagnóstico coletado e sugere rota usando IA.

    Args:
        lead_id: ID do lead
        deep_pains: Lista de dores profundas identificadas
        real_barriers: Lista de barreiras (tempo, dinheiro, etc)
        emotional_profile: Perfil emocional (ansioso, decidido, inseguro)
        investment_capacity: Capacidade (baixa, media, alta)
        urgency_level: Urgência (low, medium, high, critical)

    Returns:
        Análise com rota recomendada e justificativa
    """
    prompt = f"""Analise este diagnóstico de um lead e sugira a melhor rota:

DIAGNÓSTICO COLETADO:
- Dores profundas: {json.dumps(deep_pains, ensure_ascii=False)}
- Barreiras reais: {json.dumps(real_barriers, ensure_ascii=False)}
- Perfil emocional: {emotional_profile}
- Capacidade de investimento: {investment_capacity}
- Nível de urgência: {urgency_level}

ROTAS DISPONÍVEIS:
1. VENDAS - Lead pronto para comprar (score alto, urgência alta)
2. NUTRIÇÃO - Precisa de mais informação/relacionamento antes de comprar
3. PERDIDO - Sem fit com nosso produto ou sem budget real
4. FUTURO - Interesse genuíno mas momento errado (reativar em X meses)

Responda em JSON:
{{
    "rota_recomendada": "vendas|nutricao|perdido|futuro",
    "confianca": 0-100,
    "justificativa": "explicação detalhada",
    "proximos_passos": ["passo 1", "passo 2"],
    "sinais_positivos": ["sinal 1"],
    "sinais_negativos": ["sinal 1"],
    "se_nutricao_tempo_estimado_dias": 30,
    "se_futuro_reativar_em_meses": 6
}}
"""

    system_prompt = """Você é um especialista em qualificação de leads para profissionais e empresários.
Analise o diagnóstico e recomende a melhor rota com base nos dados.
Seja objetivo e prático. Responda APENAS com o JSON solicitado."""

    async def run_query():
        return await _query_claude(prompt, system_prompt)

    result_text = _run_async(run_query())

    try:
        clean_text = result_text.strip()
        if clean_text.startswith("```"):
            clean_text = clean_text.split("```")[1]
            if clean_text.startswith("json"):
                clean_text = clean_text[4:]
        analysis = json.loads(clean_text)
    except:
        analysis = {"raw_response": result_text}

    return {
        "lead_id": lead_id,
        "analysis": analysis,
        "analyzed_at": datetime.now().isoformat()
    }


@mcp.tool()
def generate_call_hints(
    lead_id: int,
    current_topic: str,
    lead_said: str
) -> dict:
    """
    Gera hints em tempo real durante a call.
    Use para obter sugestões de próxima pergunta ou detectar sinais.

    Args:
        lead_id: ID do lead
        current_topic: Tópico atual da conversa
        lead_said: O que o lead acabou de dizer

    Returns:
        Hints com próxima pergunta, objeções detectadas, sinais de compra
    """
    # Buscar contexto do lead
    conn = get_connection()
    cursor = conn.execute("""
        SELECT d.deep_pains, d.real_barriers, d.emotional_profile
        FROM crm_diagnosis_human d
        WHERE d.lead_id = ?
        ORDER BY d.diagnosed_at DESC LIMIT 1
    """, (lead_id,))
    diag = cursor.fetchone()

    context = ""
    if diag:
        context = f"""
Contexto anterior do lead:
- Dores conhecidas: {diag[0]}
- Barreiras conhecidas: {diag[1]}
- Perfil emocional: {diag[2]}
"""

    prompt = f"""Durante uma call de diagnóstico, o lead disse:

"{lead_said}"

Tópico atual: {current_topic}
{context}

Analise e responda em JSON:
{{
    "proxima_pergunta_sugerida": "pergunta para aprofundar",
    "objecao_detectada": "objeção identificada ou null",
    "sinal_de_compra": "sinal positivo detectado ou null",
    "emocao_percebida": "emoção do lead",
    "dica_para_vendedor": "dica prática curta"
}}
"""

    system_prompt = """Você é um assistente de vendas em tempo real.
Analise o que o lead disse e dê hints práticos e curtos.
Responda APENAS com o JSON solicitado."""

    async def run_query():
        return await _query_claude(prompt, system_prompt)

    result_text = _run_async(run_query())

    try:
        clean_text = result_text.strip()
        if clean_text.startswith("```"):
            clean_text = clean_text.split("```")[1]
            if clean_text.startswith("json"):
                clean_text = clean_text[4:]
        hints = json.loads(clean_text)
    except:
        hints = {"raw_response": result_text}

    return {
        "lead_id": lead_id,
        "hints": hints,
        "generated_at": datetime.now().isoformat()
    }


@mcp.tool()
def generate_followup_message(
    lead_id: int,
    followup_type: str,
    context: Optional[str] = None
) -> dict:
    """
    Gera mensagem personalizada para followup.

    Args:
        lead_id: ID do lead
        followup_type: Tipo (pos_venda, onboarding, check_in, renovacao, upsell, reativacao)
        context: Contexto adicional opcional

    Returns:
        Mensagem sugerida para WhatsApp/Email
    """
    # Buscar dados do lead
    conn = get_connection()
    cursor = conn.execute("""
        SELECT u.name, ls.current_state, d.deep_pains
        FROM users u
        LEFT JOIN crm_lead_state ls ON u.user_id = ls.lead_id
        LEFT JOIN crm_diagnosis_human d ON u.user_id = d.lead_id
        WHERE u.user_id = ?
    """, (lead_id,))
    row = cursor.fetchone()

    if not row:
        return {"error": f"Lead {lead_id} não encontrado"}

    lead_name = row[0]
    state = row[1]
    pains = row[2]

    prompt = f"""Gere uma mensagem de followup para WhatsApp:

Lead: {lead_name}
Tipo de followup: {followup_type}
Estado atual: {state}
Dores conhecidas: {pains}
Contexto adicional: {context or 'Nenhum'}

TIPOS DE FOLLOWUP:
- pos_venda: D+1 após compra, verificar se está tudo ok
- onboarding: D+3, ajudar no início
- check_in: D+7 ou D+30, verificar progresso
- renovacao: antes de vencer assinatura
- upsell: oferecer produto complementar
- reativacao: lead que esfriou, voltar a engajar

Responda em JSON:
{{
    "mensagem_whatsapp": "mensagem informal e personalizada",
    "mensagem_email_assunto": "assunto do email",
    "mensagem_email_corpo": "corpo do email mais formal",
    "melhor_horario": "sugestão de horário para enviar",
    "call_to_action": "ação desejada do lead"
}}
"""

    system_prompt = """Você é especialista em comunicação para profissionais e empresários.
Gere mensagens empáticas, personalizadas e que gerem resposta.
Use linguagem natural brasileira. Responda APENAS com o JSON."""

    async def run_query():
        return await _query_claude(prompt, system_prompt)

    result_text = _run_async(run_query())

    try:
        clean_text = result_text.strip()
        if clean_text.startswith("```"):
            clean_text = clean_text.split("```")[1]
            if clean_text.startswith("json"):
                clean_text = clean_text[4:]
        messages = json.loads(clean_text)
    except:
        messages = {"raw_response": result_text}

    return {
        "lead_id": lead_id,
        "lead_name": lead_name,
        "followup_type": followup_type,
        "messages": messages,
        "generated_at": datetime.now().isoformat()
    }


# ==================== DASHBOARD ====================

@mcp.tool()
def get_crm_dashboard() -> dict:
    """
    Obtém estatísticas do dashboard do CRM.

    Returns:
        Métricas do funil, tarefas pendentes, reuniões do dia, etc.
    """
    conn = get_connection()

    # Leads por estado
    cursor = conn.execute("""
        SELECT current_state, COUNT(*) as count
        FROM crm_lead_state
        GROUP BY current_state
    """)
    leads_by_state = {row[0]: row[1] for row in cursor.fetchall()}

    # Tarefas pendentes
    cursor = conn.execute("""
        SELECT COUNT(*) FROM crm_tasks WHERE status = 'open'
    """)
    open_tasks = cursor.fetchone()[0]

    # Tarefas atrasadas
    cursor = conn.execute("""
        SELECT COUNT(*) FROM crm_tasks
        WHERE status = 'open' AND due_at < datetime('now')
    """)
    overdue_tasks = cursor.fetchone()[0]

    # Reuniões de hoje
    cursor = conn.execute("""
        SELECT COUNT(*) FROM crm_meetings
        WHERE date(scheduled_at) = date('now') AND status = 'scheduled'
    """)
    meetings_today = cursor.fetchone()[0]

    # Vendas do mês
    cursor = conn.execute("""
        SELECT COUNT(*), COALESCE(SUM(final_amount), 0) FROM crm_orders
        WHERE status = 'completed'
        AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
    """)
    row = cursor.fetchone()
    sales_this_month = {"count": row[0], "total": row[1]}

    # Eventos recentes
    cursor = conn.execute("""
        SELECT event_type, COUNT(*) as count
        FROM crm_lead_events
        WHERE date(event_at) >= date('now', '-7 days')
        GROUP BY event_type
        ORDER BY count DESC
        LIMIT 10
    """)
    recent_events = {row[0]: row[1] for row in cursor.fetchall()}

    # Followups pendentes
    cursor = conn.execute("""
        SELECT COUNT(*) FROM crm_followups WHERE status = 'pending'
    """)
    pending_followups = cursor.fetchone()[0]

    # Followups para hoje
    cursor = conn.execute("""
        SELECT COUNT(*) FROM crm_followups
        WHERE date(scheduled_at) = date('now') AND status IN ('pending', 'sent')
    """)
    followups_today = cursor.fetchone()[0]

    # Diagnósticos por rota
    cursor = conn.execute("""
        SELECT recommended_route, COUNT(*) as count
        FROM crm_diagnosis_human
        WHERE diagnosed_at >= date('now', '-30 days')
        GROUP BY recommended_route
    """)
    diagnoses_by_route = {row[0]: row[1] for row in cursor.fetchall()}

    # Diagnósticos pendentes de rota
    cursor = conn.execute("""
        SELECT COUNT(*) FROM crm_diagnosis_human
        WHERE recommended_route IS NULL
    """)
    pending_route_decision = cursor.fetchone()[0]

    return {
        "leads_by_state": leads_by_state,
        "open_tasks": open_tasks,
        "overdue_tasks": overdue_tasks,
        "meetings_today": meetings_today,
        "sales_this_month": sales_this_month,
        "pending_followups": pending_followups,
        "followups_today": followups_today,
        "diagnoses_by_route_30d": diagnoses_by_route,
        "pending_route_decision": pending_route_decision,
        "recent_events_7d": recent_events
    }


if __name__ == "__main__":
    mcp.run()
