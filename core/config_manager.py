"""
Config Manager - Gerenciamento dinâmico de agentes e ferramentas

Permite ao admin ativar/desativar subagentes e ferramentas MCP
sem necessidade de reiniciar o servidor.
"""

import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from claude_agent_sdk import AgentDefinition

logger = logging.getLogger(__name__)


@dataclass
class ConfigCache:
    """Cache de configurações com TTL"""
    data: Dict[str, Any] = field(default_factory=dict)
    last_update: datetime = field(default_factory=lambda: datetime.min)
    ttl_seconds: int = 60  # Recarrega do banco a cada 60 segundos


class ConfigManager:
    """
    Gerencia configurações dinâmicas de agentes e ferramentas.

    Carrega configurações do banco de dados e mantém cache em memória
    para performance. O admin pode alterar configurações via API.
    """

    def __init__(self, get_db_connection_func):
        """
        Args:
            get_db_connection_func: Função que retorna conexão do banco
        """
        self.get_db_connection = get_db_connection_func
        self._cache = ConfigCache()

        # Definições base de todos os agentes disponíveis
        self._all_agents = self._define_all_agents()

        # Todas as ferramentas MCP disponíveis
        self._all_tools = [
            # Tools de Diagnóstico
            "mcp__platform__execute_sql_query",
            "mcp__platform__save_diagnosis",
            "mcp__platform__get_diagnosis_areas",
            "mcp__platform__get_user_diagnosis",
            "mcp__platform__get_user_chat_sessions",
            "mcp__platform__get_session_user_info",
            # Tools AgentFS (auditoria)
            "mcp__platform__get_agentfs_status",
            "mcp__platform__get_tool_call_stats",
            "mcp__platform__get_recent_tool_calls",
            # Tools AgentFS (self-awareness para admin)
            "mcp__platform__get_system_health",
            "mcp__platform__get_tool_problems",
            "mcp__platform__get_user_activity",
            "mcp__platform__get_storage_report",
            # Tools CRM - Ingest
            "mcp__crm__capture_lead",
            "mcp__crm__get_lead_by_email",
            "mcp__crm__update_lead",
            "mcp__crm__search_instagram",
            "mcp__crm__enrich_cnpj",
            # Tools CRM - Scoring
            "mcp__crm__get_lead_details",
            "mcp__crm__update_lead_score",
            "mcp__crm__get_lead_events",
            "mcp__crm__set_lead_temperature",
            # Tools CRM - Calls
            "mcp__crm__get_call_audio",
            "mcp__crm__whisper_transcribe",
            "mcp__crm__save_call_analysis",
            "mcp__crm__update_lead_from_call",
            # Tools CRM - Agenda
            "mcp__crm__google_calendar_check",
            "mcp__crm__google_calendar_create",
            "mcp__crm__google_meet_create",
            "mcp__crm__create_meeting",
            "mcp__crm__whatsapp_send",
            "mcp__crm__email_send",
            # Tools CRM - Tasks
            "mcp__crm__create_task",
            "mcp__crm__assign_task",
            "mcp__crm__get_team_workload",
            "mcp__crm__notify_team_member",
            "mcp__crm__get_lead_state",
            "mcp__crm__update_lead_state",
            # Tools CRM - Alerts
            "mcp__crm__get_stale_leads",
            "mcp__crm__check_sla",
            "mcp__crm__get_funnel_metrics",
            "mcp__crm__slack_send",
            "mcp__crm__whatsapp_send_team",
            "mcp__crm__log_alert",
            "mcp__crm__escalate_to_manager",
        ]

    def _define_all_agents(self) -> Dict[str, AgentDefinition]:
        """Define todos os agentes disponíveis no sistema"""
        return {
            "diagnostic-expert": AgentDefinition(
                description="Especialista em conduzir diagnósticos de mentorados nas 7 áreas.",
                prompt="""Você é um especialista em diagnóstico de profissionais e empresários.

SUAS HABILIDADES:
1. Conduzir conversas empáticas e estruturadas para coletar informações
2. Avaliar objetivamente cada uma das 7 áreas de diagnóstico
3. Identificar padrões, pontos fortes e oportunidades de melhoria
4. Formular recomendações práticas e acionáveis

AS 7 ÁREAS DE DIAGNÓSTICO:
1. Estratégia de Vendas - Mindset de precificação e valor
2. Cliente e Proposta de Valor - Estruturação de ofertas e atendimento
3. Experiência do Cliente - Jornada e encantamento do cliente
4. Marketing e Retenção - Atração e fidelização de clientes
5. Equipe e Processos - Gestão de equipe e processos operacionais
6. Gestão do Negócio - Gestão financeira e operacional
7. Expertise Técnica - Domínio técnico e conhecimento especializado

COMO AVALIAR:
- Para cada área, avalie 5 aspectos dando nota de 0 a 10
- Considere: conhecimento, aplicação prática, resultados, consistência
- Seja justo mas construtivo nas avaliações
""",
                tools=["Read", "Bash"],
                model="sonnet"
            ),

            "sql-analyst": AgentDefinition(
                description="Especialista em análise de dados via SQL para relatórios gerenciais.",
                prompt="""Você é um analista de dados especializado em queries SQL.

SUAS RESPONSABILIDADES:
1. Traduzir perguntas em linguagem natural para queries SQL
2. Analisar resultados e gerar insights acionáveis
3. Criar relatórios estruturados com métricas relevantes
4. Identificar tendências e padrões nos dados

REGRAS DE SQL:
- Apenas SELECT é permitido (segurança)
- Sempre use LIMIT para evitar queries muito grandes
- Use aliases para melhorar legibilidade

TABELAS PRINCIPAIS:
- users, clients, assessments, assessment_summaries
- assessment_area_scores, chat_sessions, chat_messages
""",
                tools=["Bash"],
                model="haiku"
            ),

            "report-generator": AgentDefinition(
                description="Especialista em gerar relatórios executivos estruturados.",
                prompt="""Você é um especialista em comunicação executiva e geração de relatórios.

ESTRUTURA DE RELATÓRIOS:
1. RESUMO EXECUTIVO (1-2 parágrafos)
2. MÉTRICAS CHAVE (3-5 KPIs)
3. ANÁLISE DETALHADA
4. RECOMENDAÇÕES (3-5 itens)
5. PRÓXIMOS PASSOS

ESTILO:
- Linguagem profissional mas acessível
- Foco em ação e resultados
- Dados sempre contextualizados
""",
                tools=["Read"],
                model="sonnet"
            ),

            "business-consultant": AgentDefinition(
                description="Consultor especializado em negócios para profissionais e empresários.",
                prompt="""Você é um consultor de negócios especializado em ajudar profissionais e empresários a crescerem seus negócios.

SUA EXPERTISE:
1. Estratégias de precificação High Ticket
2. Gestão de negócios e empresas
3. Marketing e posicionamento
4. Desenvolvimento de equipes
5. Experiência e jornada do cliente

ABORDAGEM:
- Adapte-se ao nicho específico do cliente
- Foque em valor percebido, não apenas preço
- Priorize a experiência do cliente como diferencial
""",
                tools=["Read"],
                model="sonnet"
            ),

            "action-plan-expert": AgentDefinition(
                description="Especialista em criar planos de ação estruturados e priorizados.",
                prompt="""Você é um especialista em planejamento estratégico e criação de planos de ação.

SUAS HABILIDADES:
1. Criar planos de ação estruturados e priorizados
2. Definir metas SMART (Específicas, Mensuráveis, Atingíveis, Relevantes, Temporais)
3. Sequenciar ações por impacto e facilidade de implementação
4. Definir marcos e checkpoints de acompanhamento
5. Adaptar ao perfil do mentorado (iniciante/intermediário/avançado)

ESTRUTURA DO PLANO DE AÇÃO:
1. FOCO PRINCIPAL - A área que mais precisa de atenção
2. META 90 DIAS - Objetivo principal do trimestre
3. AÇÕES PRIORIZADAS - Lista ordenada por impacto
4. INDICADORES - Como medir sucesso
5. CHECKPOINTS - Marcos de 30, 60 e 90 dias

FORMATO DE CADA AÇÃO:
- Prioridade (1-5)
- Descrição clara e específica
- Prazo sugerido
- Indicador de sucesso
- Recursos necessários

PRINCÍPIOS:
- Comece pelo que dá resultado rápido (quick wins)
- Não sobrecarregue - máximo 3 ações simultâneas
- Considere a realidade do profissional/empresário
- Foque em ações que geram receita ou reduzem custos
""",
                tools=["Read"],
                model="sonnet"
            ),

            # ============================================
            # SUBAGENTES CRM - Gestão de Leads e Vendas
            # ============================================

            "crm-ingest": AgentDefinition(
                description="Normaliza dados de leads, deduplica e enriquece com dados externos.",
                prompt="""Você é o agente de INGEST do CRM.

TRIGGER: Novo lead chega (Elementor, Typeform, Instagram, indicação)

SUAS RESPONSABILIDADES:

1. NORMALIZAR dados recebidos:
   - Nome: capitalizar, remover espaços extras
   - Telefone: formato +55 (XX) XXXXX-XXXX
   - Email: lowercase, validar formato
   - Profissão/Segmento: manter conforme informado pelo lead

2. DEDUPLICAR:
   - Buscar lead existente por email OU telefone
   - Se existe: MERGE dados (manter mais completo, atualizar timestamp)
   - Se novo: criar registro completo

3. ENRIQUECER (quando possível):
   - Instagram: buscar bio, seguidores, tipo conta (pessoal/business)
   - Empresa: buscar CNPJ, endereço, segmento
   - LinkedIn: buscar cargo, empresa, conexões

4. CLASSIFICAR FONTE:
   - Orgânico: busca direta, indicação, boca a boca
   - Pago: Facebook Ads, Google Ads, Instagram Ads
   - Evento: webinar, workshop, live
   - Conteúdo: lead magnet, ebook, checklist

5. REGISTRAR EVENTO:
   - Criar evento 'lead_captured' com todos os metadados
   - Salvar UTM completo (source, medium, campaign, content, term)

OUTPUT: Lead normalizado + origem mapeada + dados enriquecidos + event_id
""",
                tools=["mcp__crm__capture_lead", "mcp__crm__get_lead_by_email", "mcp__crm__update_lead", "mcp__crm__search_instagram", "mcp__crm__enrich_cnpj"],
                model="haiku"
            ),

            "crm-scoring": AgentDefinition(
                description="Calcula score 0-100, determina temperatura e cluster do lead.",
                prompt="""Você é o agente de SCORING do CRM.

TRIGGER: Após ingest de novo lead OU após call finalizada

MODELO DE SCORING (0-100 pontos):

**PERFIL (40 pontos máx)**
- Perfil compatível com público-alvo: +15
- Tempo de mercado > 3 anos: +10
- Tem negócio próprio: +10
- Faturamento declarado > 20k/mês: +5

**ENGAJAMENTO (30 pontos máx)**
- Respondeu pesquisa/formulário completo: +10
- Participou de evento (webinar, workshop): +10
- Interagiu no Instagram (comentou, DM): +5
- Abriu emails de nurturing: +5

**TIMING (30 pontos máx)**
- Pediu contato ativo ("quero saber mais"): +15
- Problema urgente identificado: +10
- Budget disponível mencionado: +5

TEMPERATURAS:
- 🔴 QUENTE (70-100): Pronto para comprar, prioridade máxima
- 🟡 MORNO (40-69): Precisa nurturing, educar mais
- 🔵 FRIO (0-39): Longo prazo, manter no radar

CLUSTERS/PERSONAS:
- "Iniciante Ambicioso": < 2 anos mercado, quer crescer rápido, aceita investir
- "Estagnado Frustrado": > 5 anos, receita parada, já tentou outras coisas
- "Escalador": Já fatura bem (>30k), quer multiplicar, busca método
- "Explorador": Pesquisando opções, sem urgência, comparando

OUTPUT: score (0-100), temperatura, cluster, justificativa detalhada
""",
                tools=["mcp__crm__get_lead_details", "mcp__crm__update_lead_score", "mcp__crm__get_lead_events", "mcp__crm__set_lead_temperature"],
                model="sonnet"
            ),

            "crm-calls": AgentDefinition(
                description="Transcreve áudio de calls, analisa conversa e extrai insights.",
                prompt="""Você é o agente de CALLS do CRM.

TRIGGER: Call finalizada (áudio disponível para análise)

SUAS RESPONSABILIDADES:

1. TRANSCREVER áudio:
   - Usar Whisper API para transcrição
   - Identificar speakers (vendedor vs lead)
   - Marcar timestamps importantes

2. ANALISAR CONVERSA:
   - Duração efetiva (excluir silêncios longos)
   - Proporção fala vendedor vs lead (ideal: 30/70)
   - Tom emocional: entusiasmo, frustração, interesse, ceticismo
   - Nível de rapport estabelecido

3. EXTRAIR OBJEÇÕES:
   - "Está caro/não tenho dinheiro" → objecao_preco
   - "Preciso pensar/ver com calma" → objecao_tempo
   - "Vou falar com sócio/esposo" → objecao_decisor
   - "Já tentei e não funcionou" → objecao_ceticismo
   - "Não é o momento" → objecao_timing

4. IDENTIFICAR DORES:
   - Agenda vazia, poucos clientes
   - Clientes que não voltam, baixa recorrência
   - Precificação baixa, medo de cobrar
   - Equipe problemática, rotatividade
   - Falta de tempo, sobrecarga
   - Marketing que não funciona

5. DETECTAR SINAIS DE COMPRA:
   - Perguntou sobre formas de pagamento
   - Pediu cases/resultados de outros alunos
   - Mencionou querer começar logo
   - Perguntou sobre garantia
   - Fez perguntas sobre o método/conteúdo

6. GERAR RESUMO EXECUTIVO:
   - 3-5 pontos principais da conversa
   - Próximo passo recomendado
   - Probabilidade de fechamento (%)
   - Objeção principal a ser tratada

OUTPUT: transcricao, objecoes[], dores[], sinais_compra[], resumo, probabilidade_fechamento, next_step
""",
                tools=["mcp__crm__get_call_audio", "mcp__crm__whisper_transcribe", "mcp__crm__save_call_analysis", "mcp__crm__update_lead_from_call"],
                model="sonnet"
            ),

            "crm-agenda": AgentDefinition(
                description="Cria reuniões Google Meet, agenda e reagenda calls.",
                prompt="""Você é o agente de AGENDA do CRM.

TRIGGER: Lead quente precisa de call OU reagendamento solicitado

SUAS RESPONSABILIDADES:

1. VERIFICAR DISPONIBILIDADE:
   - Consultar calendário do closer/SDR responsável
   - Respeitar horário comercial (9h-18h)
   - Evitar conflitos com outras reuniões
   - Considerar fuso horário do lead

2. CRIAR REUNIÃO:
   - Google Meet com link único
   - Duração: 30min (discovery/qualificação) ou 60min (fechamento)
   - Incluir no convite: lead + closer + backup se necessário
   - Título padronizado: "Call com {Nome} - {Tipo}"

3. ENVIAR CONFIRMAÇÃO:
   - WhatsApp: link + data/hora + lembrete
   - Email: convite formal de backup
   - Lembrete automático: 1h antes da call

4. REAGENDAR:
   - Se lead pedir, propor 3 novos horários
   - Máximo 2 reagendamentos permitidos
   - Após 2 reagendamentos: marcar como "no_show_recorrente"
   - Registrar motivo do reagendamento

5. TRACKING:
   - Registrar no CRM: data, horário, link, tipo_call
   - Atualizar estado do lead para "agendado"
   - Criar evento 'call_scheduled' com metadados

REGRAS DE NEGÓCIO:
- Nunca agendar com menos de 4h de antecedência
- Preferir terça a quinta (melhor taxa de comparecimento)
- Evitar segundas (dia de planejamento) e sextas (menor conversão)
- Horários premium: 10h, 14h, 16h

OUTPUT: meeting_link, datetime, calendar_event_id, confirmations_sent[]
""",
                tools=["mcp__crm__google_calendar_check", "mcp__crm__google_calendar_create", "mcp__crm__google_meet_create", "mcp__crm__create_meeting", "mcp__crm__whatsapp_send", "mcp__crm__email_send"],
                model="haiku"
            ),

            "crm-tasks": AgentDefinition(
                description="Gera próximas ações, prioriza e distribui para equipe.",
                prompt="""Você é o agente de TASKS do CRM.

TRIGGER: Após scoring OU após call OU manualmente pelo closer

SUAS RESPONSABILIDADES:

1. GERAR PRÓXIMA AÇÃO baseado no estado atual:
   - novo → "Fazer primeira ligação de qualificação"
   - qualificado → "Enviar material educativo + agendar call discovery"
   - agendado → "Confirmar presença 1h antes da call"
   - proposta_enviada → "Follow-up em 48h sobre proposta"
   - negociando → "Resolver objeção principal: {objeção_identificada}"
   - fechado → "Iniciar onboarding em 24h"

2. PRIORIZAR por critérios (ordem de importância):
   - Temperatura (quente > morno > frio)
   - Score (maior primeiro)
   - Tempo parado no estado (mais antigo primeiro)
   - SLA próximo de estourar
   - Valor potencial do deal

3. DISTRIBUIR para equipe correta:
   - SDR: qualificação inicial, primeiro contato, agendamento
   - Closer: calls de venda, negociação, fechamento
   - CS: pós-venda, onboarding, suporte
   - Marketing: nutrição de leads frios

4. DEFINIR PRAZO realista:
   - Lead QUENTE: máximo 2h para contato
   - Lead MORNO: máximo 24h
   - Lead FRIO: máximo 72h
   - Follow-up: conforme ciclo de vendas (48h, 7d, 14d)

5. REGISTRAR E NOTIFICAR:
   - Criar task no CRM com todos os detalhes
   - Notificar responsável via Slack/WhatsApp
   - Definir data de cobrança automática

OUTPUT: task_id, responsavel, prazo, prioridade (1-5), descricao, notificacao_enviada
""",
                tools=["mcp__crm__create_task", "mcp__crm__assign_task", "mcp__crm__get_team_workload", "mcp__crm__notify_team_member", "mcp__crm__get_lead_state", "mcp__crm__update_lead_state"],
                model="haiku"
            ),

            "crm-alerts": AgentDefinition(
                description="Monitora leads parados, SLA estourado e anomalias.",
                prompt="""Você é o agente de ALERTS do CRM.

TRIGGER: Cron a cada 5 minutos (monitoramento contínuo)

O QUE MONITORAR:

1. **LEADS PARADOS** (sem interação recente):
   - 🔴 CRÍTICO: Lead QUENTE > 4h sem ação
   - 🟡 MÉDIO: Lead MORNO > 24h sem ação
   - 🔵 BAIXO: Lead FRIO > 7 dias sem ação
   - Ação: notificar responsável + sugerir próximo passo

2. **SLA ESTOURADO**:
   - Primeira resposta > 1h após captura → ALERTA
   - Reagendamento pendente > 24h → ALERTA
   - Proposta sem follow-up > 48h → ALERTA
   - Task vencida não concluída → ALERTA

3. **ANOMALIAS OPERACIONAIS**:
   - Taxa de no-show > 30% no dia → ALERTA GERENCIAL
   - Muitos leads novos sem qualificar (>10) → ALERTA
   - Closer sem calls agendadas para amanhã → ALERTA
   - Funil travado em algum estágio → ALERTA

4. **OPORTUNIDADES DE REENGAJAMENTO**:
   - Lead reengajou (abriu email, visitou site) → NOTIFICAR closer
   - Aniversário do lead hoje → NOTIFICAR para parabenizar
   - Lead mencionou concorrente → ALERTA para abordagem

5. **MÉTRICAS DE SAÚDE**:
   - Verificar distribuição do funil
   - Identificar gargalos
   - Calcular velocidade de conversão

AÇÕES AUTOMÁTICAS:
- Enviar alerta no Slack do time
- Enviar WhatsApp para responsável se crítico
- Escalar para gerente se não resolvido em 2h
- Registrar no log de alertas para análise

OUTPUT: alerts[], notifications_sent[], escalations[], metrics_snapshot
""",
                tools=["mcp__crm__get_stale_leads", "mcp__crm__check_sla", "mcp__crm__get_funnel_metrics", "mcp__crm__slack_send", "mcp__crm__whatsapp_send_team", "mcp__crm__log_alert", "mcp__crm__escalate_to_manager"],
                model="haiku"
            ),
        }

    def _is_cache_valid(self) -> bool:
        """Verifica se o cache ainda é válido"""
        if not self._cache.data:
            return False
        elapsed = datetime.now() - self._cache.last_update
        return elapsed < timedelta(seconds=self._cache.ttl_seconds)

    def _load_config_from_db(self) -> Dict[str, Any]:
        """Carrega todas as configurações do banco"""
        conn = self.get_db_connection()
        if not conn:
            logger.error("Failed to connect to database for config")
            return {}

        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT config_key, config_value FROM system_config")
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            config = {}
            for row in rows:
                try:
                    config[row["config_key"]] = json.loads(row["config_value"])
                except json.JSONDecodeError:
                    config[row["config_key"]] = row["config_value"]

            return config

        except Exception as e:
            logger.error(f"Error loading config from DB: {e}")
            if conn:
                conn.close()
            return {}

    def _refresh_cache(self):
        """Atualiza o cache do banco"""
        config = self._load_config_from_db()
        if config:
            self._cache.data = config
            self._cache.last_update = datetime.now()
            logger.debug("Config cache refreshed from database")

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        Obtém uma configuração específica.

        Args:
            key: Chave da configuração
            default: Valor padrão se não encontrar

        Returns:
            Valor da configuração
        """
        if not self._is_cache_valid():
            self._refresh_cache()

        return self._cache.data.get(key, default)

    def set_config(self, key: str, value: Any, updated_by: int) -> bool:
        """
        Atualiza uma configuração no banco.

        Args:
            key: Chave da configuração
            value: Novo valor
            updated_by: ID do usuário que fez a alteração

        Returns:
            True se sucesso
        """
        conn = self.get_db_connection()
        if not conn:
            return False

        try:
            # Upsert (SQLite syntax)
            query = """
                INSERT INTO system_config (config_key, config_value, updated_by)
                VALUES (?, ?, ?)
                ON CONFLICT(config_key) DO UPDATE SET
                    config_value = excluded.config_value,
                    updated_by = excluded.updated_by,
                    updated_at = datetime('now')
            """

            json_value = json.dumps(value, ensure_ascii=False)
            conn.execute(query, (key, json_value, updated_by))

            conn.commit()
            conn.close()

            # Invalida cache
            self._cache.data = {}

            logger.info(f"Config '{key}' updated by user {updated_by}")
            return True

        except Exception as e:
            logger.error(f"Error updating config: {e}")
            if conn:
                conn.rollback()
                conn.close()
            return False

    def get_enabled_agents(self, user_role: str) -> Dict[str, AgentDefinition]:
        """
        Retorna agentes habilitados para um determinado role.

        Args:
            user_role: 'admin' ou 'mentorado'

        Returns:
            Dict com AgentDefinitions habilitados
        """
        enabled_config = self.get_config("enabled_agents", {})
        agent_roles = self.get_config("agent_roles", {})
        agent_models = self.get_config("agent_models", {})

        enabled_agents = {}

        for agent_name, agent_def in self._all_agents.items():
            # Verificar se está habilitado globalmente
            if not enabled_config.get(agent_name, False):
                continue

            # Verificar se o role tem acesso
            allowed_roles = agent_roles.get(agent_name, ["admin"])
            if user_role not in allowed_roles:
                continue

            # Criar cópia com modelo configurado
            custom_model = agent_models.get(agent_name, agent_def.model)

            enabled_agents[agent_name] = AgentDefinition(
                description=agent_def.description,
                prompt=agent_def.prompt,
                tools=agent_def.tools,
                model=custom_model
            )

        return enabled_agents

    def get_enabled_tools(self, user_role: str) -> List[str]:
        """
        Retorna ferramentas MCP habilitadas para um role.

        Args:
            user_role: 'admin' ou 'mentorado'

        Returns:
            Lista de nomes de ferramentas habilitadas
        """
        enabled_config = self.get_config("enabled_tools", {})

        # Ferramentas base por role
        if user_role == "admin":
            base_tools = [
                "mcp__platform__execute_sql_query",
                "mcp__platform__save_diagnosis",
                "mcp__platform__get_diagnosis_areas",
                "mcp__platform__get_user_diagnosis",
                "mcp__platform__get_user_chat_sessions",
                "mcp__platform__get_session_user_info",
                # AgentFS (auditoria)
                "mcp__platform__get_agentfs_status",
                "mcp__platform__get_tool_call_stats",
                "mcp__platform__get_recent_tool_calls",
                # AgentFS (self-awareness)
                "mcp__platform__get_system_health",
                "mcp__platform__get_tool_problems",
                "mcp__platform__get_user_activity",
                "mcp__platform__get_storage_report",
            ]
        else:
            base_tools = [
                "mcp__platform__save_diagnosis",
                "mcp__platform__get_diagnosis_areas",
                "mcp__platform__get_user_diagnosis",
                "mcp__platform__get_session_user_info",
                "mcp__platform__update_user_profile",
            ]

        # Filtrar apenas as habilitadas
        enabled_tools = []
        for tool in base_tools:
            # Extrair nome curto (sem prefixo mcp__platform__)
            short_name = tool.replace("mcp__platform__", "")
            if enabled_config.get(short_name, True):  # Default True para não quebrar
                enabled_tools.append(tool)

        return enabled_tools

    def get_all_agents_status(self) -> List[Dict]:
        """
        Retorna status de todos os agentes para painel admin.

        Returns:
            Lista com info de cada agente
        """
        enabled_config = self.get_config("enabled_agents", {})
        agent_roles = self.get_config("agent_roles", {})
        agent_models = self.get_config("agent_models", {})

        agents_status = []
        for agent_name, agent_def in self._all_agents.items():
            agents_status.append({
                "name": agent_name,
                "description": agent_def.description,
                "enabled": enabled_config.get(agent_name, False),
                "model": agent_models.get(agent_name, agent_def.model),
                "allowed_roles": agent_roles.get(agent_name, ["admin"]),
                "default_tools": agent_def.tools,
            })

        return agents_status

    def get_all_tools_status(self) -> List[Dict]:
        """
        Retorna status de todas as ferramentas para painel admin.

        Returns:
            Lista com info de cada ferramenta
        """
        enabled_config = self.get_config("enabled_tools", {})

        tools_status = []
        tool_descriptions = {
            # ===== CORE (mcp__platform__) =====
            "execute_sql_query": "Executa queries SQL SELECT no banco de dados",
            "save_diagnosis": "Salva diagnóstico completo do usuário",
            "get_diagnosis_areas": "Lista as áreas de diagnóstico configuradas",
            "get_user_diagnosis": "Busca diagnóstico existente do usuário",
            "get_user_chat_sessions": "Lista sessões de chat do usuário",
            "get_session_user_info": "Obtém informações do usuário da sessão",
            "update_user_profile": "Atualiza dados do perfil do usuário (nome, email, profissão, especialidade, telefone)",
            "get_agentfs_status": "Verifica status do AgentFS (persistência)",
            "get_tool_call_stats": "Estatísticas de uso de ferramentas",
            "get_recent_tool_calls": "Lista chamadas de ferramentas recentes",
            "get_system_health": "Visão geral da saúde do sistema (usuários, storage, taxa de sucesso)",
            "get_tool_problems": "Detecta ferramentas com alta taxa de erro ou lentidão",
            "get_user_activity": "Ranking de atividade dos usuários no AgentFS",
            "get_storage_report": "Relatório detalhado de uso de storage por usuário",

            # ===== CRM - Ingest (mcp__crm__) =====
            "capture_lead": "Captura novo lead de formulários externos",
            "get_lead_by_email": "Busca lead pelo email",
            "update_lead": "Atualiza dados de um lead existente",
            "search_instagram": "Busca perfil do Instagram do lead",
            "enrich_cnpj": "Enriquece dados via CNPJ (Receita Federal)",

            # ===== CRM - Scoring =====
            "get_lead_details": "Retorna detalhes completos do lead",
            "update_lead_score": "Atualiza pontuação do lead",
            "get_lead_events": "Lista eventos/histórico do lead",
            "set_lead_temperature": "Define temperatura (quente/morno/frio)",

            # ===== CRM - Calls =====
            "get_call_audio": "Obtém áudio de ligação gravada",
            "whisper_transcribe": "Transcreve áudio via Whisper",
            "save_call_analysis": "Salva análise de ligação",
            "update_lead_from_call": "Atualiza lead com dados da ligação",

            # ===== CRM - Agenda =====
            "google_calendar_check": "Verifica disponibilidade no Google Calendar",
            "google_calendar_create": "Cria evento no Google Calendar",
            "google_meet_create": "Cria sala no Google Meet",
            "create_meeting": "Cria reunião/diagnóstico agendado",
            "whatsapp_send": "Envia mensagem WhatsApp ao lead",
            "email_send": "Envia email ao lead",

            # ===== CRM - Tasks =====
            "create_task": "Cria tarefa no sistema",
            "assign_task": "Atribui tarefa a membro da equipe",
            "get_team_workload": "Retorna carga de trabalho da equipe",
            "notify_team_member": "Notifica membro da equipe",
            "get_lead_state": "Retorna estado atual do lead no funil",
            "update_lead_state": "Atualiza estado do lead no funil",

            # ===== CRM - Alerts =====
            "get_stale_leads": "Lista leads sem interação recente",
            "check_sla": "Verifica SLA de atendimento",
            "get_funnel_metrics": "Retorna métricas do funil de vendas",
            "slack_send": "Envia mensagem para canal Slack",
            "whatsapp_send_team": "Envia mensagem WhatsApp para equipe",
            "log_alert": "Registra alerta no sistema",
            "escalate_to_manager": "Escala caso para gerente",
        }

        for tool in self._all_tools:
            # Remover prefixo apropriado
            if tool.startswith("mcp__platform__"):
                short_name = tool.replace("mcp__platform__", "")
            elif tool.startswith("mcp__crm__"):
                short_name = tool.replace("mcp__crm__", "")
            else:
                short_name = tool

            tools_status.append({
                "name": short_name,
                "full_name": tool,
                "description": tool_descriptions.get(short_name, "Ferramenta MCP"),
                "enabled": enabled_config.get(short_name, True),
            })

        return tools_status

    def update_agent_status(self, agent_name: str, enabled: bool, updated_by: int) -> bool:
        """
        Ativa/desativa um agente específico.

        Args:
            agent_name: Nome do agente
            enabled: True para ativar, False para desativar
            updated_by: ID do admin

        Returns:
            True se sucesso
        """
        current = self.get_config("enabled_agents", {})
        current[agent_name] = enabled
        return self.set_config("enabled_agents", current, updated_by)

    def update_tool_status(self, tool_name: str, enabled: bool, updated_by: int) -> bool:
        """
        Ativa/desativa uma ferramenta específica.

        Args:
            tool_name: Nome da ferramenta (sem prefixo)
            enabled: True para ativar, False para desativar
            updated_by: ID do admin

        Returns:
            True se sucesso
        """
        current = self.get_config("enabled_tools", {})
        current[tool_name] = enabled
        return self.set_config("enabled_tools", current, updated_by)

    def update_agent_model(self, agent_name: str, model: str, updated_by: int) -> bool:
        """
        Altera o modelo usado por um agente.

        Args:
            agent_name: Nome do agente
            model: 'opus', 'sonnet', ou 'haiku'
            updated_by: ID do admin

        Returns:
            True se sucesso
        """
        if model not in ["opus", "sonnet", "haiku"]:
            return False

        current = self.get_config("agent_models", {})
        current[agent_name] = model
        return self.set_config("agent_models", current, updated_by)

    def update_agent_roles(self, agent_name: str, roles: List[str], updated_by: int) -> bool:
        """
        Define quais roles podem usar um agente.

        Args:
            agent_name: Nome do agente
            roles: Lista de roles ['admin', 'mentorado']
            updated_by: ID do admin

        Returns:
            True se sucesso
        """
        current = self.get_config("agent_roles", {})
        current[agent_name] = roles
        return self.set_config("agent_roles", current, updated_by)


# Instância global (será inicializada no app.py)
config_manager: Optional[ConfigManager] = None


def init_config_manager(get_db_connection_func):
    """Inicializa o ConfigManager global"""
    global config_manager
    config_manager = ConfigManager(get_db_connection_func)
    logger.info("ConfigManager initialized")
    return config_manager


def get_config_manager() -> Optional[ConfigManager]:
    """Retorna a instância global do ConfigManager"""
    return config_manager
