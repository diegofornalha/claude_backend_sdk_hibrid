#!/usr/bin/env python3
"""
CRM Orquestrador - Agente Central com Claude Agent SDK + Turso Local

Recebe eventos e coordena o fluxo entre as tools do CRM.
Baseado na arquitetura: Orquestrador → Subagentes (tools)
"""

import os
import sys
import json
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Any
import libsql_experimental as libsql

# Claude Agent SDK
SDK_PATH = "/home/diagnostico/diagnostico_nanda/claude-agent-sdk-python/src"
if SDK_PATH not in sys.path:
    sys.path.insert(0, SDK_PATH)

# Configuração
DATABASE_PATH = os.getenv(
    "TURSO_DATABASE_PATH",
    "/home/diagnostico/.turso/databases/crm.db"
)

# System Prompt do Orquestrador
ORCHESTRATOR_SYSTEM_PROMPT = """
# ORQUESTRADOR CRM - VENDAS HIGH TICKET

Você é o agente central de um CRM inteligente para gestão de leads e vendas.

## CONTEXTO DO NEGÓCIO
- Vendemos mentoria/consultoria de alto valor
- Ticket médio: R$ 12.000
- Processo: Captação → Diagnóstico → Venda → Acompanhamento
- Público: Profissionais e empresários que buscam crescimento

## ESTADOS DO LEAD
- novo: Acabou de entrar
- diagnostico_pendente: Aguardando agendamento
- diagnostico_agendado: Call marcada
- em_atendimento: Em processo de venda/nutrição
- proposta_enviada: Proposta enviada
- negociacao: Em negociação
- produto_vendido: Comprou
- followup_ativo: Pós-venda
- perdido: Não comprou (com motivo)
- reativacao: Para futuro

## REGRAS DE DECISÃO

### Evento: new_lead
1. Criar lead_state como "novo"
2. Calcular score/temperatura
3. Se score >= 70: estado → "diagnostico_pendente", criar task "agendar_reuniao"
4. Se score < 70: estado → "em_atendimento", criar task "ligar" ou "enviar_material"
5. Registrar evento

### Evento: diagnosis_completed
1. Analisar diagnóstico
2. Recalcular score
3. Definir rota (vendas/nutricao/perdido/futuro)
4. Criar tasks apropriadas
5. Atualizar estado

### Evento: meeting_scheduled
1. Atualizar estado → "diagnostico_agendado"
2. Criar task de preparação
3. Registrar evento

### Evento: meeting_completed
1. Atualizar reunião
2. Criar task para próxima ação
3. Registrar evento

### Evento: order_created
1. Atualizar estado
2. Registrar evento

### Evento: order_completed (venda fechada)
1. Atualizar estado → "produto_vendido"
2. Criar followups automáticos (D+1, D+3, D+7, D+30)
3. Registrar evento

### Evento: lead_lost
1. Atualizar estado → "perdido"
2. Registrar motivo
3. Registrar evento

## OUTPUT
Sempre retorne JSON:
{
  "event_processed": "tipo_evento",
  "actions_taken": ["ação1", "ação2"],
  "lead_state": "novo_estado",
  "tasks_created": ["task1"],
  "next_steps": ["próximo passo"]
}
"""


def get_connection():
    """Retorna conexão com o banco Turso Local"""
    conn = libsql.connect(DATABASE_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def generate_id(prefix: str = ""):
    """Gera ID único"""
    import uuid
    timestamp = int(datetime.now().timestamp())
    unique = uuid.uuid4().hex[:8]
    return f"{prefix}{timestamp}.{unique}" if prefix else f"{timestamp}.{unique}"


class CRMOrchestrator:
    """
    Orquestrador Central do CRM.

    Recebe eventos e coordena as ações usando as tools do MCP CRM.
    """

    def __init__(self):
        self.conn = get_connection()

    async def process_event(self, event_type: str, payload: dict) -> dict:
        """
        Processa um evento e executa as ações necessárias.

        Args:
            event_type: Tipo do evento (new_lead, diagnosis_completed, etc)
            payload: Dados do evento

        Returns:
            Resultado com ações tomadas
        """
        result = {
            "event_processed": event_type,
            "actions_taken": [],
            "lead_state": None,
            "tasks_created": [],
            "next_steps": [],
            "timestamp": datetime.now().isoformat()
        }

        try:
            if event_type == "new_lead":
                result = await self._handle_new_lead(payload, result)

            elif event_type == "diagnosis_completed":
                result = await self._handle_diagnosis_completed(payload, result)

            elif event_type == "meeting_scheduled":
                result = await self._handle_meeting_scheduled(payload, result)

            elif event_type == "meeting_completed":
                result = await self._handle_meeting_completed(payload, result)

            elif event_type == "order_created":
                result = await self._handle_order_created(payload, result)

            elif event_type == "order_completed":
                result = await self._handle_order_completed(payload, result)

            elif event_type == "lead_lost":
                result = await self._handle_lead_lost(payload, result)

            elif event_type == "followup_due":
                result = await self._handle_followup_due(payload, result)

            else:
                result["error"] = f"Evento desconhecido: {event_type}"

        except Exception as e:
            result["error"] = str(e)

        return result

    # ==================== HANDLERS ====================

    async def _handle_new_lead(self, payload: dict, result: dict) -> dict:
        """Processa novo lead"""
        lead_id = payload.get("lead_id") or payload.get("user_id")

        if not lead_id:
            result["error"] = "lead_id não fornecido"
            return result

        # 1. Criar/atualizar estado do lead
        self._upsert_lead_state(lead_id, "novo")
        result["actions_taken"].append("lead_state_created")

        # 2. Gerar briefing/score usando IA
        briefing = await self._generate_briefing(lead_id)
        result["actions_taken"].append("briefing_generated")

        # 3. Determinar próxima ação baseado no score
        score = briefing.get("briefing", {}).get("score_estimado", 50)
        temperature = briefing.get("briefing", {}).get("temperatura", "warm")

        if score >= 70:
            # Lead quente - agendar diagnóstico
            new_state = "diagnostico_pendente"
            task_type = "agendar_reuniao"
            task_priority = "high"
            ai_recommendation = "Lead quente! Agendar diagnóstico o mais rápido possível."
        elif score >= 40:
            # Lead morno - ligar para qualificar
            new_state = "em_atendimento"
            task_type = "ligar"
            task_priority = "medium"
            ai_recommendation = "Ligar para qualificar e entender melhor as necessidades."
        else:
            # Lead frio - nutrir
            new_state = "em_atendimento"
            task_type = "enviar_material"
            task_priority = "low"
            ai_recommendation = "Enviar material educativo para aquecer o lead."

        # 4. Atualizar estado
        self._update_lead_state(lead_id, new_state)
        result["lead_state"] = new_state
        result["actions_taken"].append("state_updated")

        # 5. Criar task
        task_id = self._create_task(
            lead_id=lead_id,
            task_type=task_type,
            priority=task_priority,
            ai_recommendation=ai_recommendation
        )
        result["tasks_created"].append(task_type)
        result["actions_taken"].append("task_created")

        # 6. Registrar evento
        self._log_event(lead_id, "lead_captured", {
            "score": score,
            "temperature": temperature,
            "initial_state": new_state
        })
        result["actions_taken"].append("event_logged")

        result["next_steps"] = [
            f"Executar task: {task_type}",
            "Acompanhar resposta do lead"
        ]

        return result

    async def _handle_diagnosis_completed(self, payload: dict, result: dict) -> dict:
        """Processa diagnóstico completado"""
        lead_id = payload.get("lead_id")
        diagnosis_data = payload.get("diagnosis", {})

        if not lead_id:
            result["error"] = "lead_id não fornecido"
            return result

        # 1. Salvar diagnóstico
        diagnosis_id = self._save_diagnosis(lead_id, diagnosis_data)
        result["actions_taken"].append("diagnosis_saved")

        # 2. Analisar e sugerir rota
        route_analysis = await self._analyze_route(lead_id, diagnosis_data)
        result["actions_taken"].append("route_analyzed")

        recommended_route = route_analysis.get("analysis", {}).get("rota_recomendada", "nutricao")

        # 3. Atualizar estado baseado na rota
        state_map = {
            "vendas": "proposta_enviada",
            "nutricao": "em_atendimento",
            "perdido": "perdido",
            "futuro": "reativacao"
        }
        new_state = state_map.get(recommended_route, "em_atendimento")
        self._update_lead_state(lead_id, new_state)
        result["lead_state"] = new_state
        result["actions_taken"].append("state_updated")

        # 4. Criar tasks baseado na rota
        if recommended_route == "vendas":
            task_id = self._create_task(
                lead_id=lead_id,
                task_type="enviar_proposta",
                priority="high",
                ai_recommendation="Lead qualificado para venda! Enviar proposta."
            )
            result["tasks_created"].append("enviar_proposta")

        elif recommended_route == "nutricao":
            task_id = self._create_task(
                lead_id=lead_id,
                task_type="followup",
                priority="medium",
                ai_recommendation="Lead precisa de nutrição. Manter contato regular."
            )
            result["tasks_created"].append("followup")

        elif recommended_route == "futuro":
            # Criar followup de reativação
            self._create_followup(
                lead_id=lead_id,
                followup_type="reativacao",
                days_from_now=90
            )
            result["tasks_created"].append("reativacao_90d")

        # 5. Registrar evento
        self._log_event(lead_id, "diagnosis_completed", {
            "diagnosis_id": diagnosis_id,
            "recommended_route": recommended_route
        })
        result["actions_taken"].append("event_logged")

        result["next_steps"] = route_analysis.get("analysis", {}).get("proximos_passos", [])

        return result

    async def _handle_meeting_scheduled(self, payload: dict, result: dict) -> dict:
        """Processa reunião agendada"""
        lead_id = payload.get("lead_id")
        meeting_id = payload.get("meeting_id")
        scheduled_at = payload.get("scheduled_at")

        if not lead_id:
            result["error"] = "lead_id não fornecido"
            return result

        # 1. Atualizar estado
        self._update_lead_state(lead_id, "diagnostico_agendado")
        result["lead_state"] = "diagnostico_agendado"
        result["actions_taken"].append("state_updated")

        # 2. Criar task de preparação
        task_id = self._create_task(
            lead_id=lead_id,
            task_type="preparar_call",
            priority="medium",
            ai_recommendation="Preparar briefing antes da call.",
            due_at=scheduled_at
        )
        result["tasks_created"].append("preparar_call")
        result["actions_taken"].append("task_created")

        # 3. Registrar evento
        self._log_event(lead_id, "meeting_scheduled", {
            "meeting_id": meeting_id,
            "scheduled_at": scheduled_at
        })
        result["actions_taken"].append("event_logged")

        result["next_steps"] = [
            "Preparar briefing do lead",
            "Confirmar presença 24h antes"
        ]

        return result

    async def _handle_meeting_completed(self, payload: dict, result: dict) -> dict:
        """Processa reunião completada"""
        lead_id = payload.get("lead_id")
        meeting_id = payload.get("meeting_id")
        outcome = payload.get("outcome", "completed")

        if not lead_id:
            result["error"] = "lead_id não fornecido"
            return result

        # 1. Atualizar reunião
        self._update_meeting_status(meeting_id, outcome)
        result["actions_taken"].append("meeting_updated")

        # 2. Criar task para próxima ação
        if outcome == "completed":
            task_id = self._create_task(
                lead_id=lead_id,
                task_type="followup",
                priority="high",
                ai_recommendation="Fazer follow-up da call. Qual foi a decisão?"
            )
            result["tasks_created"].append("followup_call")
        elif outcome == "no_show":
            task_id = self._create_task(
                lead_id=lead_id,
                task_type="ligar",
                priority="high",
                ai_recommendation="No-show! Ligar para reagendar."
            )
            result["tasks_created"].append("reagendar")

        result["actions_taken"].append("task_created")

        # 3. Registrar evento
        self._log_event(lead_id, f"meeting_{outcome}", {
            "meeting_id": meeting_id
        })
        result["actions_taken"].append("event_logged")

        return result

    async def _handle_order_created(self, payload: dict, result: dict) -> dict:
        """Processa pedido criado"""
        lead_id = payload.get("lead_id")
        order_id = payload.get("order_id")

        if not lead_id:
            result["error"] = "lead_id não fornecido"
            return result

        # 1. Atualizar estado
        self._update_lead_state(lead_id, "negociacao")
        result["lead_state"] = "negociacao"
        result["actions_taken"].append("state_updated")

        # 2. Criar task de cobrança
        task_id = self._create_task(
            lead_id=lead_id,
            task_type="cobrar_pagamento",
            priority="high",
            ai_recommendation="Acompanhar pagamento do pedido."
        )
        result["tasks_created"].append("cobrar_pagamento")
        result["actions_taken"].append("task_created")

        # 3. Registrar evento
        self._log_event(lead_id, "order_created", {
            "order_id": order_id
        })
        result["actions_taken"].append("event_logged")

        return result

    async def _handle_order_completed(self, payload: dict, result: dict) -> dict:
        """Processa venda fechada"""
        lead_id = payload.get("lead_id")
        order_id = payload.get("order_id")

        if not lead_id:
            result["error"] = "lead_id não fornecido"
            return result

        # 1. Atualizar estado
        self._update_lead_state(lead_id, "produto_vendido")
        result["lead_state"] = "produto_vendido"
        result["actions_taken"].append("state_updated")

        # 2. Criar followups automáticos
        followups = [
            ("pos_venda", 1),      # D+1
            ("onboarding", 3),     # D+3
            ("check_in", 7),       # D+7
            ("check_in", 30),      # D+30
        ]

        for followup_type, days in followups:
            self._create_followup(
                lead_id=lead_id,
                followup_type=followup_type,
                days_from_now=days,
                order_id=order_id
            )
            result["tasks_created"].append(f"{followup_type}_D+{days}")

        result["actions_taken"].append("followups_created")

        # 3. Atualizar estado para followup ativo
        self._update_lead_state(lead_id, "followup_ativo")
        result["lead_state"] = "followup_ativo"

        # 4. Registrar evento
        self._log_event(lead_id, "product_sold", {
            "order_id": order_id
        })
        result["actions_taken"].append("event_logged")

        result["next_steps"] = [
            "Executar followup D+1 (pos_venda)",
            "Garantir onboarding do cliente"
        ]

        return result

    async def _handle_lead_lost(self, payload: dict, result: dict) -> dict:
        """Processa lead perdido"""
        lead_id = payload.get("lead_id")
        loss_reason = payload.get("reason", "Não informado")

        if not lead_id:
            result["error"] = "lead_id não fornecido"
            return result

        # 1. Atualizar estado
        self._update_lead_state(lead_id, "perdido", notes=loss_reason)
        result["lead_state"] = "perdido"
        result["actions_taken"].append("state_updated")

        # 2. Registrar evento
        self._log_event(lead_id, "lost", {
            "reason": loss_reason
        })
        result["actions_taken"].append("event_logged")

        # 3. Agendar reativação futura (opcional)
        if payload.get("schedule_reactivation", False):
            self._create_followup(
                lead_id=lead_id,
                followup_type="reativacao",
                days_from_now=180
            )
            result["tasks_created"].append("reativacao_180d")

        return result

    async def _handle_followup_due(self, payload: dict, result: dict) -> dict:
        """Processa followup que está no prazo"""
        followup_id = payload.get("followup_id")
        lead_id = payload.get("lead_id")

        if not lead_id:
            result["error"] = "lead_id não fornecido"
            return result

        # 1. Gerar mensagem personalizada
        followup_type = payload.get("followup_type", "check_in")
        message = await self._generate_followup_message(lead_id, followup_type)
        result["actions_taken"].append("message_generated")

        # 2. Criar task para enviar
        task_id = self._create_task(
            lead_id=lead_id,
            task_type="enviar_followup",
            priority="medium",
            ai_recommendation=message.get("messages", {}).get("mensagem_whatsapp", "Enviar followup")
        )
        result["tasks_created"].append("enviar_followup")
        result["actions_taken"].append("task_created")

        result["generated_message"] = message

        return result

    # ==================== HELPERS ====================

    def _upsert_lead_state(self, lead_id: int, state: str, **kwargs):
        """Cria ou atualiza estado do lead"""
        cursor = self.conn.execute(
            "SELECT lead_id FROM crm_lead_state WHERE lead_id = ?",
            (lead_id,)
        )
        exists = cursor.fetchone()

        now = datetime.now().isoformat()

        if exists:
            self.conn.execute("""
                UPDATE crm_lead_state
                SET current_state = ?, state_updated_at = ?
                WHERE lead_id = ?
            """, (state, now, lead_id))
        else:
            self.conn.execute("""
                INSERT INTO crm_lead_state (lead_id, current_state, state_updated_at)
                VALUES (?, ?, ?)
            """, (lead_id, state, now))

        self.conn.commit()

    def _update_lead_state(self, lead_id: int, state: str, **kwargs):
        """Atualiza estado do lead"""
        notes = kwargs.get("notes")
        now = datetime.now().isoformat()

        if notes:
            self.conn.execute("""
                UPDATE crm_lead_state
                SET current_state = ?, state_updated_at = ?, notes = ?
                WHERE lead_id = ?
            """, (state, now, notes, lead_id))
        else:
            self.conn.execute("""
                UPDATE crm_lead_state
                SET current_state = ?, state_updated_at = ?
                WHERE lead_id = ?
            """, (state, now, lead_id))

        self.conn.commit()

    def _create_task(self, lead_id: int, task_type: str, priority: str = "medium",
                     ai_recommendation: str = None, due_at: str = None) -> str:
        """Cria uma task"""
        task_id = generate_id("task_")

        if not due_at:
            # Default: amanhã às 9h
            tomorrow = datetime.now() + timedelta(days=1)
            due_at = tomorrow.replace(hour=9, minute=0, second=0).isoformat()

        self.conn.execute("""
            INSERT INTO crm_tasks
            (task_id, lead_id, task_type, priority, status, due_at, ai_recommendation)
            VALUES (?, ?, ?, ?, 'open', ?, ?)
        """, (task_id, lead_id, task_type, priority, due_at, ai_recommendation))

        self.conn.commit()
        return task_id

    def _create_followup(self, lead_id: int, followup_type: str,
                         days_from_now: int, order_id: str = None) -> str:
        """Cria um followup"""
        followup_id = generate_id("fu_")
        scheduled_at = (datetime.now() + timedelta(days=days_from_now)).isoformat()

        self.conn.execute("""
            INSERT INTO crm_followups
            (followup_id, lead_id, order_id, followup_type, scheduled_at, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        """, (followup_id, lead_id, order_id, followup_type, scheduled_at))

        self.conn.commit()
        return followup_id

    def _save_diagnosis(self, lead_id: int, data: dict) -> str:
        """Salva diagnóstico humano"""
        diagnosis_id = generate_id("diag_")

        self.conn.execute("""
            INSERT INTO crm_diagnosis_human
            (diagnosis_id, lead_id, deep_pains, real_barriers, emotional_profile,
             investment_capacity, urgency_level, recommended_route, route_justification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            diagnosis_id,
            lead_id,
            json.dumps(data.get("deep_pains", [])),
            json.dumps(data.get("real_barriers", [])),
            data.get("emotional_profile"),
            data.get("investment_capacity"),
            data.get("urgency_level"),
            data.get("recommended_route"),
            data.get("route_justification")
        ))

        self.conn.commit()
        return diagnosis_id

    def _update_meeting_status(self, meeting_id: str, status: str):
        """Atualiza status da reunião"""
        self.conn.execute("""
            UPDATE crm_meetings SET status = ? WHERE meeting_id = ?
        """, (status, meeting_id))
        self.conn.commit()

    def _log_event(self, lead_id: int, event_type: str, payload: dict = None):
        """Registra evento de auditoria"""
        event_id = generate_id("evt_")

        self.conn.execute("""
            INSERT INTO crm_lead_events
            (event_id, lead_id, event_type, actor_type, channel, payload)
            VALUES (?, ?, ?, 'orchestrator', 'system', ?)
        """, (event_id, lead_id, event_type, json.dumps(payload) if payload else None))

        self.conn.commit()

    # ==================== IA HELPERS ====================

    async def _generate_briefing(self, lead_id: int) -> dict:
        """Gera briefing usando Claude Agent SDK"""
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                TextBlock,
                query,
            )

            # Buscar dados do lead
            cursor = self.conn.execute("""
                SELECT name, email, phone FROM users WHERE user_id = ?
            """, (lead_id,))
            user = cursor.fetchone()

            if not user:
                return {"briefing": {"score_estimado": 50, "temperatura": "warm"}}

            prompt = f"""Analise este lead e gere um briefing rápido:

Lead: {user[0]}
Email: {user[1]}
Phone: {user[2]}

Responda em JSON:
{{
    "score_estimado": 0-100,
    "temperatura": "cold|warm|hot|burning",
    "talking_points": ["ponto1", "ponto2"]
}}
"""

            options = ClaudeAgentOptions(
                system_prompt="Você é um especialista em vendas. Responda APENAS com JSON.",
                max_turns=1,
            )

            result_text = ""
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            result_text += block.text

            # Parse JSON
            clean_text = result_text.strip()
            if clean_text.startswith("```"):
                clean_text = clean_text.split("```")[1]
                if clean_text.startswith("json"):
                    clean_text = clean_text[4:]

            return {"briefing": json.loads(clean_text)}

        except Exception as e:
            return {"briefing": {"score_estimado": 50, "temperatura": "warm", "error": str(e)}}

    async def _analyze_route(self, lead_id: int, diagnosis_data: dict) -> dict:
        """Analisa diagnóstico e sugere rota"""
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                TextBlock,
                query,
            )

            prompt = f"""Analise este diagnóstico e sugira a melhor rota:

DIAGNÓSTICO:
{json.dumps(diagnosis_data, indent=2, ensure_ascii=False)}

ROTAS:
- vendas: Lead pronto para comprar
- nutricao: Precisa de mais tempo
- perdido: Sem fit
- futuro: Interesse mas timing errado

Responda em JSON:
{{
    "rota_recomendada": "vendas|nutricao|perdido|futuro",
    "confianca": 0-100,
    "proximos_passos": ["passo1", "passo2"]
}}
"""

            options = ClaudeAgentOptions(
                system_prompt="Você é um especialista em qualificação de leads. Responda APENAS com JSON.",
                max_turns=1,
            )

            result_text = ""
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            result_text += block.text

            clean_text = result_text.strip()
            if clean_text.startswith("```"):
                clean_text = clean_text.split("```")[1]
                if clean_text.startswith("json"):
                    clean_text = clean_text[4:]

            return {"analysis": json.loads(clean_text)}

        except Exception as e:
            return {"analysis": {"rota_recomendada": "nutricao", "error": str(e)}}

    async def _generate_followup_message(self, lead_id: int, followup_type: str) -> dict:
        """Gera mensagem de followup"""
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                TextBlock,
                query,
            )

            # Buscar nome do lead
            cursor = self.conn.execute("""
                SELECT name FROM users WHERE user_id = ?
            """, (lead_id,))
            user = cursor.fetchone()
            lead_name = user[0] if user else "Cliente"

            prompt = f"""Gere mensagem de followup para WhatsApp:

Lead: {lead_name}
Tipo: {followup_type}

Responda em JSON:
{{
    "mensagem_whatsapp": "mensagem informal e personalizada"
}}
"""

            options = ClaudeAgentOptions(
                system_prompt="Você gera mensagens empáticas em português brasileiro. Responda APENAS com JSON.",
                max_turns=1,
            )

            result_text = ""
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            result_text += block.text

            clean_text = result_text.strip()
            if clean_text.startswith("```"):
                clean_text = clean_text.split("```")[1]
                if clean_text.startswith("json"):
                    clean_text = clean_text[4:]

            return {"messages": json.loads(clean_text)}

        except Exception as e:
            return {"messages": {"mensagem_whatsapp": f"Olá! Como posso ajudar?", "error": str(e)}}


# ==================== API ====================

async def process_event(event_type: str, payload: dict) -> dict:
    """Função principal para processar eventos"""
    orchestrator = CRMOrchestrator()
    return await orchestrator.process_event(event_type, payload)


def process_event_sync(event_type: str, payload: dict) -> dict:
    """Versão síncrona para uso em MCP"""
    return asyncio.run(process_event(event_type, payload))


# ==================== MAIN ====================

if __name__ == "__main__":
    # Teste básico
    import asyncio

    async def test():
        orchestrator = CRMOrchestrator()

        # Teste: novo lead
        result = await orchestrator.process_event("new_lead", {
            "lead_id": 1,
            "source": "test"
        })

        print(json.dumps(result, indent=2, ensure_ascii=False))

    asyncio.run(test())
