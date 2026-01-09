"""
Configuracao de logging estruturado para o backend.

Features:
- Logs em formato JSON (para producao)
- Logs formatados (para desenvolvimento)
- Rotacao automatica de arquivos
- Niveis de log configuraveis
- Contexto adicional (request_id, user_id, etc.)
"""

import logging
import logging.handlers
import sys
import os
import json
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path
import threading

# Contexto local por thread para informacoes de request
_local = threading.local()


class JSONFormatter(logging.Formatter):
    """Formatter que produz JSON estruturado."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Adicionar contexto da thread (request_id, user_id, etc.)
        if hasattr(_local, "context"):
            log_data.update(_local.context)

        # Adicionar excecao se houver
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Adicionar campos extras do record
        if hasattr(record, "extra_data"):
            log_data["extra"] = record.extra_data

        return json.dumps(log_data, default=str)


class ColoredFormatter(logging.Formatter):
    """Formatter com cores para desenvolvimento."""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        reset = self.RESET if color else ""

        # Formato: [TIME] LEVEL MODULE:LINE - MESSAGE
        formatted = (
            f"{color}[{datetime.now().strftime('%H:%M:%S')}] "
            f"{record.levelname:8}{reset} "
            f"{record.module}:{record.lineno} - "
            f"{record.getMessage()}"
        )

        # Adicionar excecao se houver
        if record.exc_info:
            formatted += f"\n{self.formatException(record.exc_info)}"

        return formatted


class ContextLogger(logging.Logger):
    """Logger com suporte a contexto adicional."""

    def _log(self, level, msg, args, exc_info=None, extra=None, stack_info=False, **kwargs):
        if extra is None:
            extra = {}

        # Adicionar contexto da thread
        if hasattr(_local, "context"):
            extra["extra_data"] = {**getattr(_local, "context", {}), **extra.get("extra_data", {})}

        super()._log(level, msg, args, exc_info, extra, stack_info)


def set_context(**kwargs):
    """Define contexto para logs da thread atual."""
    if not hasattr(_local, "context"):
        _local.context = {}
    _local.context.update(kwargs)


def clear_context():
    """Limpa contexto da thread atual."""
    if hasattr(_local, "context"):
        _local.context = {}


def get_context() -> Dict[str, Any]:
    """Retorna contexto atual."""
    return getattr(_local, "context", {})


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
):
    """
    Configura logging para a aplicacao.

    Args:
        level: Nivel de log (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: Se True, usa formato JSON (producao)
        log_file: Caminho para arquivo de log (opcional)
        max_bytes: Tamanho maximo do arquivo antes de rotacionar
        backup_count: Numero de arquivos de backup a manter
    """
    # Usar nossa classe de logger customizada
    logging.setLoggerClass(ContextLogger)

    # Obter nivel
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Configurar root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remover handlers existentes
    root_logger.handlers.clear()

    # Escolher formatter
    if json_format:
        formatter = JSONFormatter()
    else:
        formatter = ColoredFormatter()

    # Handler para console (stderr)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Handler para arquivo (opcional)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        # Arquivo sempre em JSON para facilitar parsing
        file_handler.setFormatter(JSONFormatter())
        file_handler.setLevel(log_level)
        root_logger.addHandler(file_handler)

    # Silenciar loggers barulhentos
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)

    # Log de inicializacao
    logger = logging.getLogger(__name__)
    logger.info(
        "Logging configurado",
        extra={"extra_data": {"level": level, "json_format": json_format, "log_file": log_file}}
    )


def get_logger(name: str) -> logging.Logger:
    """
    Obtem um logger configurado.

    Args:
        name: Nome do logger (geralmente __name__)

    Returns:
        Logger configurado
    """
    return logging.getLogger(name)


# Configuracao automatica baseada em variaveis de ambiente
def auto_configure():
    """Configura logging automaticamente baseado em variaveis de ambiente."""
    env = os.getenv("ENVIRONMENT", "development")
    level = os.getenv("LOG_LEVEL", "INFO")
    log_file = os.getenv("LOG_FILE")

    json_format = env in ("production", "staging")

    setup_logging(
        level=level,
        json_format=json_format,
        log_file=log_file,
    )


# Middleware para FastAPI
class LoggingMiddleware:
    """Middleware para adicionar contexto de request aos logs."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        import uuid

        # Gerar request_id unico
        request_id = str(uuid.uuid4())[:8]

        # Extrair informacoes do request
        path = scope.get("path", "")
        method = scope.get("method", "")

        # Definir contexto para logs desta request
        set_context(
            request_id=request_id,
            path=path,
            method=method,
        )

        logger = get_logger("http")
        logger.info(f"Request: {method} {path}")

        try:
            await self.app(scope, receive, send)
        finally:
            clear_context()
