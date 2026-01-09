"""
CRM Agent Orchestrator - Orquestração automática do funil de vendas usando Claude Agent SDK

Este módulo integra o Claude Agent SDK com as ferramentas MCP do CRM Nanda
para processar leads automaticamente através do funil de vendas.
"""

import logging
import asyncio
from typing import Dict, Any, AsyncIterator, Optional
from datetime import datetime

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AgentDefinition,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)


class CRMOrchestrator:
    """
    Orquestrador que usa Claude Agent SDK para processar leads automaticamente.

    Encadeia os agentes CRM:
    1. crm-scoring: Calcula score e temperatura
    2. crm-tasks: Cria próxima ação
    3. crm-agenda: Agenda call se lead quente
    """

    def __init__(self):
        """Inicializa o orquestrador com definições de agentes"""

        # Definir agentes CRM
        self.agents = {
            "crm-scoring": AgentDefinition(
                description="Calcula score 0-100 e determina temperatura do lead (frio/morno/quente)",
                prompt="""Você é o agente de SCORING do CRM Nanda.

MODELO DE SCORING (0-100 pontos):

**PERFIL (40 pontos máx)**
- Profissão/nicho compatível com público-alvo: +15
- Tempo de mercado > 3 anos: +10
- Tem negócio próprio: +10
- Faturamento > 20k/mês: +5

**ENGAJAMENTO (30 pontos máx)**
- Respondeu formulário completo: +10
- Participou de evento: +10
- Interagiu Instagram: +5
- Abriu emails: +5

**TIMING (30 pontos máx)**
- Pediu contato ativo: +15
- Problema urgente: +10
- Budget mencionado: +5

TEMPERATURAS:
- 🔴 QUENTE (70-100): Prioridade máxima
- 🟡 MORNO (40-69): Precisa nutrição
- 🔵 FRIO (0-39): Longo prazo

Use as ferramentas MCP para:
1. Obter dados do lead (get_lead_state, get_lead_events)
2. Calcular o score baseado nos critérios acima
3. Salvar score e temperatura (update_lead_intelligence)
4. Retornar JSON com: score, temperatura, cluster, justificativa
""",
                tools=[],  # SDK vai usar allowed_tools global
                model="sonnet"
            ),

            "crm-tasks": AgentDefinition(
                description="Cria tarefas e distribui para equipe baseado na temperatura do lead",
                prompt="""Você é o agente de TASKS do CRM Nanda.

Baseado na temperatura do lead, crie a tarefa apropriada:

**LEAD QUENTE (score >= 70):**
- Tipo: ligar
- Prazo: 2h
- Prioridade: high
- Time: vendas

**LEAD MORNO (score 40-69):**
- Tipo: followup
- Prazo: 24h
- Prioridade: medium
- Time: vendas

**LEAD FRIO (score < 40):**
- Tipo: followup
- Prazo: 72h
- Prioridade: low
- Time: marketing

Use as ferramentas MCP:
1. Obter estado do lead (get_lead_state)
2. Criar tarefa (create_task)
3. Atualizar estado do lead (update_lead_state)
4. Retornar JSON com: task_id, tipo, prazo, prioridade
""",
                tools=[],
                model="haiku"
            ),

            "crm-agenda": AgentDefinition(
                description="Agenda reunião Google Meet para leads quentes",
                prompt="""Você é o agente de AGENDA do CRM Nanda.

REGRAS:
- Apenas para leads QUENTES (score >= 70)
- Duração: 30min (discovery) ou 60min (fechamento)
- Horários preferidos: 10h, 14h, 16h
- Dias: terça a quinta

Use as ferramentas MCP:
1. Criar reunião (schedule_meeting)
2. Atualizar estado para 'diagnostico_agendado'
3. Retornar JSON com: meeting_id, link, datetime
""",
                tools=[],
                model="haiku"
            )
        }

        # Configurar opções com todas as ferramentas MCP do CRM
        self.options = ClaudeAgentOptions(
            agents=self.agents,
            allowed_tools=[
                # Ferramentas MCP CRM
                "mcp__nanda-crm__get_lead_state",
                "mcp__nanda-crm__get_lead_events",
                "mcp__nanda-crm__update_lead_intelligence",
                "mcp__nanda-crm__create_task",
                "mcp__nanda-crm__update_lead_state",
                "mcp__nanda-crm__schedule_meeting",
                "mcp__nanda-crm__log_lead_event",
                # Ferramentas básicas
                "Read", "Write"
            ],
            permission_mode='acceptEdits',
            max_turns=10,
            cwd="/home/diagnostico/diagnostico_nanda/claude_backend_sdk_hibrid"
        )

    async def process_new_lead(self, lead_id: int) -> Dict[str, Any]:
        """
        Processa um novo lead pelo funil completo.

        Args:
            lead_id: ID do lead a processar

        Returns:
            Resultado do processamento com score, tarefa criada, etc.
        """
        logger.info(f"🚀 Iniciando processamento automático do lead {lead_id}")

        result = {
            "lead_id": lead_id,
            "score": None,
            "temperatura": None,
            "task_id": None,
            "meeting_id": None,
            "messages": [],
            "success": False
        }

        try:
            async with ClaudeSDKClient(options=self.options) as client:
                # Pedir processamento completo
                prompt = f"""Processe o lead ID {lead_id} pelo funil CRM completo:

1. Use o agente crm-scoring para:
   - Obter dados do lead
   - Calcular score (0-100)
   - Determinar temperatura
   - Salvar no banco

2. Baseado no score, use o agente crm-tasks para:
   - Criar tarefa apropriada
   - Atualizar estado do lead
   - Definir prazo e prioridade

3. Se lead QUENTE (score >= 70), use crm-agenda para:
   - Agendar call de diagnóstico

Retorne um resumo JSON ao final com:
- score
- temperatura
- task_id
- meeting_id (se agendado)
- próximos passos
"""

                await client.query(prompt)

                # Coletar resposta
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                result["messages"].append(block.text)
                                logger.info(f"Claude: {block.text[:100]}...")
                            elif isinstance(block, ToolUseBlock):
                                logger.info(f"🔧 Usando ferramenta: {block.name}")

                    elif isinstance(message, ResultMessage):
                        result["success"] = True
                        if message.total_cost_usd:
                            result["cost_usd"] = message.total_cost_usd
                            logger.info(f"💰 Custo: ${message.total_cost_usd:.4f}")

                logger.info(f"✅ Processamento do lead {lead_id} concluído")
                return result

        except Exception as e:
            logger.error(f"❌ Erro ao processar lead {lead_id}: {e}")
            result["error"] = str(e)
            return result

    async def analyze_call(self, lead_id: int, meeting_id: str) -> Dict[str, Any]:
        """
        Analisa uma call finalizada e atualiza o lead.

        Args:
            lead_id: ID do lead
            meeting_id: ID da reunião

        Returns:
            Análise da call
        """
        logger.info(f"🎙️ Analisando call do lead {lead_id}")

        try:
            async with ClaudeSDKClient(options=self.options) as client:
                prompt = f"""Analise a call do lead ID {lead_id} (meeting {meeting_id}):

1. Use crm-calls para analisar a conversa (se houver transcrição)
2. Use crm-scoring para recalcular score
3. Use crm-tasks para criar próxima ação

Retorne JSON com análise completa.
"""
                await client.query(prompt)

                result = {"lead_id": lead_id, "meeting_id": meeting_id, "messages": []}

                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                result["messages"].append(block.text)
                    elif isinstance(message, ResultMessage):
                        result["success"] = True

                return result

        except Exception as e:
            logger.error(f"❌ Erro ao analisar call: {e}")
            return {"error": str(e)}

    async def check_alerts(self) -> Dict[str, Any]:
        """
        Verifica leads parados, SLA estourado e anomalias.

        Returns:
            Lista de alertas gerados
        """
        logger.info("🔔 Verificando alertas do CRM")

        try:
            async with ClaudeSDKClient(options=self.options) as client:
                prompt = """Use o agente crm-alerts para verificar:

1. Leads parados (sem ação recente)
2. SLA estourado
3. Anomalias no funil

Para cada problema encontrado, crie tarefa corretiva.
Retorne JSON com lista de alertas.
"""
                await client.query(prompt)

                result = {"alerts": [], "messages": []}

                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                result["messages"].append(block.text)
                    elif isinstance(message, ResultMessage):
                        result["success"] = True

                return result

        except Exception as e:
            logger.error(f"❌ Erro ao verificar alertas: {e}")
            return {"error": str(e)}


# Instância global
_orchestrator: Optional[CRMOrchestrator] = None


def get_orchestrator() -> CRMOrchestrator:
    """Retorna a instância global do orquestrador"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = CRMOrchestrator()
        logger.info("✅ CRMOrchestrator inicializado")
    return _orchestrator
