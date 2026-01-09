"""
Multi-Provider LLM System

Suporta múltiplos providers de LLM com prioridade:
1. Claude (via assinatura) - todas as features
2. MiniMax - API compatível com Anthropic
3. OpenRouter - múltiplos modelos

Uso:
    from core.llm_provider import get_llm_response, is_using_claude

    if is_using_claude():
        # Usar claude_agent_sdk
    else:
        response = await get_llm_response(messages, system_prompt)
"""

import os
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator
from abc import ABC, abstractmethod

from anthropic import Anthropic

from core.config_manager import get_config_manager

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Interface base para providers LLM"""

    @abstractmethod
    def generate(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str = "",
        **kwargs
    ) -> str:
        pass

    @abstractmethod
    async def generate_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str = "",
        **kwargs
    ):
        """Gera resposta em streaming - retorna async generator"""
        pass


class MinimaxProvider(LLMProvider):
    """
    Provider para MiniMax API usando SDK Anthropic compatível.

    MiniMax oferece endpoint compatível com Anthropic em:
    https://api.minimax.io/anthropic
    """

    def __init__(self, api_key: str, model: str = "MiniMax-M2"):
        self.api_key = api_key
        self.model = model
        # Cliente Anthropic apontando para MiniMax
        self.client = Anthropic(
            base_url="https://api.minimax.io/anthropic",
            api_key=api_key
        )
        logger.info(f"MinimaxProvider initialized with model: {model}")

    def generate(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str = "",
        **kwargs
    ) -> str:
        """Gera resposta usando MiniMax API (via SDK Anthropic)"""

        # Preparar mensagens no formato Anthropic
        anthropic_messages = []
        for msg in messages:
            anthropic_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt or "Responda em português brasileiro.",
                messages=anthropic_messages,
                temperature=0.7
            )
            return response.content[0].text.strip()

        except Exception as e:
            logger.error(f"MiniMax error: {e}")
            raise

    async def generate_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str = "",
        **kwargs
    ):
        """Gera resposta em streaming usando MiniMax API (httpx async com SSE)"""
        import httpx
        import json

        # Preparar mensagens no formato Anthropic
        anthropic_messages = []
        for msg in messages:
            anthropic_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })

        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system_prompt or "Responda em português brasileiro.",
            "messages": anthropic_messages,
            "stream": True,
            "temperature": 0.7
        }

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }

        try:
            logger.info(f"MiniMax streaming request starting...")
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    "https://api.minimax.io/anthropic/v1/messages",
                    json=payload,
                    headers=headers
                ) as response:
                    response.raise_for_status()
                    chunk_count = 0
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        logger.debug(f"MiniMax SSE line: {line[:100]}...")
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                logger.info(f"MiniMax streaming done. Total chunks: {chunk_count}")
                                break
                            try:
                                data = json.loads(data_str)
                                event_type = data.get("type", "unknown")
                                # Formato Anthropic: {"type": "content_block_delta", "delta": {"text": "..."}}
                                if event_type == "content_block_delta":
                                    delta = data.get("delta", {})
                                    text = delta.get("text", "")
                                    if text:
                                        chunk_count += 1
                                        logger.info(f"MiniMax chunk #{chunk_count}: '{text[:30]}...'")
                                        yield text
                            except json.JSONDecodeError as je:
                                logger.warning(f"MiniMax JSON decode error: {je}")
        except Exception as e:
            logger.error(f"MiniMax stream error: {e}")
            raise


class OpenRouterProvider(LLMProvider):
    """Provider para OpenRouter API usando SDK Anthropic compatível."""

    def __init__(self, api_key: str, model: str = "openai/gpt-4o"):
        self.api_key = api_key
        self.model = model
        # OpenRouter também suporta SDK Anthropic
        self.client = Anthropic(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )
        logger.info(f"OpenRouterProvider initialized with model: {model}")

    def generate(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str = "",
        **kwargs
    ) -> str:
        """Gera resposta usando OpenRouter API"""
        import httpx

        openrouter_messages = []
        if system_prompt:
            openrouter_messages.append({
                "role": "system",
                "content": system_prompt
            })

        for msg in messages:
            openrouter_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })

        payload = {
            "model": self.model,
            "messages": openrouter_messages,
            "max_tokens": 4096,
            "temperature": 0.7
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nandamac.cloud",
            "X-Title": "Nanda Assistant"
        }

        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()

                if "choices" in data and len(data["choices"]) > 0:
                    return data["choices"][0]["message"]["content"]
                else:
                    logger.error(f"OpenRouter response format unexpected: {data}")
                    return "Erro ao processar resposta do OpenRouter."

        except Exception as e:
            logger.error(f"OpenRouter error: {e}")
            raise

    async def generate_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str = "",
        **kwargs
    ):
        """Gera resposta em streaming usando OpenRouter API (httpx async)"""
        import httpx
        import json

        openrouter_messages = []
        if system_prompt:
            openrouter_messages.append({
                "role": "system",
                "content": system_prompt
            })

        for msg in messages:
            openrouter_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })

        payload = {
            "model": self.model,
            "messages": openrouter_messages,
            "stream": True,
            "max_tokens": 4096,
            "temperature": 0.7
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nandamac.cloud",
            "X-Title": "Nanda Assistant"
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload,
                    headers=headers
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                if "choices" in data:
                                    delta = data["choices"][0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        yield content
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            logger.error(f"OpenRouter stream error: {e}")
            raise


def get_configured_provider() -> tuple[str, str, Optional[str]]:
    """Retorna provider, model e api_key configurados"""
    config_mgr = get_config_manager()
    if not config_mgr:
        return "claude", "claude-opus-4-5", None

    provider = config_mgr.get_config("llm_provider", "claude")
    model = config_mgr.get_config("llm_model", "claude-opus-4-5")
    api_key = config_mgr.get_config("llm_api_key", None)

    return provider, model, api_key


def get_llm_provider() -> Optional[LLMProvider]:
    """
    Factory para obter o provider LLM configurado.

    Retorna None se for Claude (usa claude_agent_sdk diretamente).
    Retorna MinimaxProvider ou OpenRouterProvider se configurado.
    Para modo híbrido, retorna MiniMax (usado quando não precisa de tools).
    """
    provider, model, api_key = get_configured_provider()

    logger.info(f"Getting LLM provider: {provider} / {model}")

    if provider == "claude":
        # Claude usa o claude_agent_sdk diretamente
        return None

    if provider == "hybrid":
        # Modo híbrido: usar MiniMax para respostas rápidas
        # (Claude é usado quando needs_tools() retorna True)
        if not api_key:
            logger.warning("Hybrid mode selected but no MiniMax API key configured")
            return None
        return MinimaxProvider(api_key=api_key, model="MiniMax-M2")

    if provider == "minimax":
        if not api_key:
            logger.warning("MiniMax selected but no API key configured")
            return None
        return MinimaxProvider(api_key=api_key, model=model)

    if provider == "openrouter":
        if not api_key:
            logger.warning("OpenRouter selected but no API key configured")
            return None
        return OpenRouterProvider(api_key=api_key, model=model)

    logger.warning(f"Unknown provider: {provider}, falling back to Claude")
    return None


def get_llm_response(
    messages: List[Dict[str, str]],
    system_prompt: str = "",
    stream: bool = False
):
    """
    Função de conveniência para obter resposta do LLM configurado.

    Se o provider for Claude, retorna None (caller deve usar claude_agent_sdk).
    Se for MiniMax ou OpenRouter, retorna a resposta diretamente.
    """
    provider = get_llm_provider()

    if provider is None:
        # Claude - caller deve usar claude_agent_sdk
        return None

    if stream:
        return provider.generate_stream(messages, system_prompt)
    else:
        return provider.generate(messages, system_prompt)


def is_using_claude() -> bool:
    """Verifica se o provider atual é Claude (puro, não híbrido)"""
    provider, _, _ = get_configured_provider()
    return provider == "claude"


def is_hybrid_mode() -> bool:
    """Verifica se está no modo híbrido (MiniMax + Claude)"""
    provider, _, _ = get_configured_provider()
    return provider == "hybrid"


def needs_tools(message: str) -> bool:
    """
    Detecta se a mensagem provavelmente precisa de ferramentas.

    Retorna True para mensagens que parecem precisar de:
    - Consultas ao banco de dados
    - Diagnósticos e avaliações
    - Listagens e relatórios
    - Estatísticas e métricas
    - Ações administrativas (CRUD)
    - Geração de arquivos/gráficos
    """
    message_lower = message.lower()

    # Palavras-chave que indicam necessidade de ferramentas
    tool_keywords = [
        # Consultas e buscas
        "quantos", "quantas", "listar", "liste", "buscar", "busque",
        "consultar", "consulte", "mostrar", "mostre", "exibir", "exiba",
        "total", "todos", "todas", "estatísticas", "estatistica",
        "pesquisar", "pesquise", "encontrar", "encontre", "procurar",
        "verificar", "verifique", "checar", "cheque",

        # Diagnóstico e avaliação
        "diagnóstico", "diagnostico", "avaliar", "avaliação", "avaliacao",
        "salvar diagnóstico", "salvar diagnostico", "fazer diagnóstico",
        "iniciar diagnóstico", "continuar diagnóstico", "finalizar diagnóstico",
        "7 áreas", "sete áreas", "área de", "pontuação", "score",

        # Dados do sistema
        "mentorado", "mentorados", "mentor", "mentores",
        "usuário", "usuario", "usuários", "usuarios",
        "sessão", "sessao", "sessões", "sessoes",
        "banco de dados", "tabela", "tabelas", "registro", "registros",
        "cadastro", "cadastrados", "ativos", "inativos",

        # Ações administrativas (CRUD)
        "criar", "deletar", "excluir", "atualizar", "editar",
        "adicionar", "remover", "modificar", "alterar",
        "salvar", "gravar", "inserir", "cadastrar",
        # Solicitações de atualização de perfil
        "atualize minha", "atualize meu", "mude minha", "mude meu",
        "altere minha", "altere meu", "troque minha", "troque meu",
        "modifique minha", "modifique meu", "corrija minha", "corrija meu",

        # Relatórios e exportação
        "relatório", "relatorio", "exportar", "gerar pdf", "gerar relatório",
        "resumo", "sumário", "sumario", "histórico", "historico",

        # Gráficos e visualização
        "gráfico", "grafico", "chart", "visualizar", "plotar",
        "evolução", "evolucao", "tendência", "tendencia", "comparar",

        # Agendamento e lembretes
        "agendar", "agenda", "lembrete", "notificar", "enviar",

        # Status do sistema e infraestrutura
        "funcionando", "está funcionando", "está rodando", "status",
        "agentfs", "banco", "servidor", "sistema", "infraestrutura",
        "conexão", "conexao", "conectado", "online", "offline",
        "health", "healthcheck", "saúde", "saude",

        # SQL direto (detectar injeção ou queries)
        "select", "insert", "update", "delete", "from", "where",

        # Perguntas sobre dados (indicam consulta)
        "qual é", "qual o", "quais são", "quais os", "quem são", "quem é",
        "existe", "existem", "tem cadastrado", "estão cadastrados",
        "último", "ultima", "primeiro", "primeira", "mais recente",
    ]

    # Padrões de perguntas que indicam necessidade de tools
    question_patterns = [
        "me mostre", "me liste", "me diga", "pode listar", "pode mostrar",
        "gostaria de ver", "quero ver", "preciso saber", "preciso ver",
        "como está", "como estão", "qual a situação", "status de",
        # Perguntas sobre o próprio usuário (requer lookup)
        "sobre mim", "sabe sobre mim", "meus dados", "minhas informações",
        "meu perfil", "meu diagnóstico", "minha avaliação", "meu histórico",
        "quem sou", "o que sabe", "o que você sabe", "o que vc sabe",
        "meu cadastro", "minha conta", "meu nome", "meu email",
    ]

    # Verifica keywords diretas
    if any(keyword in message_lower for keyword in tool_keywords):
        return True

    # Verifica padrões de perguntas
    if any(pattern in message_lower for pattern in question_patterns):
        return True

    return False
