"""
WhatsApp OTP - Envio de códigos de verificação via WhatsApp

Usa o OpenClaw (Baileys) para enviar mensagens via WhatsApp.
Gateway rodando na porta 18789.

Uso:
    from core.whatsapp_otp import send_whatsapp_otp, normalize_phone_number
"""

import re
import subprocess
import logging

logger = logging.getLogger(__name__)

OPENCLAW_BIN = "/tmp/npm-global/bin/openclaw"


def normalize_phone_number(phone: str) -> str:
    """
    Normaliza número de telefone para formato E.164 (+55...).

    Aceita:
        - 11999999999 → +5511999999999
        - 5511999999999 → +5511999999999
        - +5511999999999 → +5511999999999
        - (11) 99999-9999 → +5511999999999

    Raises:
        ValueError: se o número não é válido
    """
    # Remove tudo que não é dígito ou +
    cleaned = re.sub(r'[^\d+]', '', phone)

    # Remove + do início para trabalhar só com dígitos
    if cleaned.startswith('+'):
        cleaned = cleaned[1:]

    # Se já começa com 55 e tem 12-13 dígitos, é BR completo
    if cleaned.startswith('55') and len(cleaned) in (12, 13):
        return f'+{cleaned}'

    # Se tem 10-11 dígitos, é número BR sem código do país
    if len(cleaned) in (10, 11):
        return f'+55{cleaned}'

    # Se tem 12-13 dígitos mas não começa com 55, assume BR
    if len(cleaned) in (12, 13):
        return f'+{cleaned}'

    raise ValueError(
        f'Número de telefone inválido: {phone}. '
        'Use formato E.164: +5511999999999 ou 11999999999'
    )


def send_whatsapp_otp(phone: str, otp: str) -> bool:
    """
    Envia OTP via WhatsApp usando OpenClaw CLI.

    Args:
        phone: Número normalizado E.164 (+5511999999999)
        otp: Código OTP de 6 dígitos

    Returns:
        True se enviou com sucesso, False caso contrário
    """
    message = f"🔐 Seu código de verificação: *{otp}*\n\nVálido por 10 minutos. Não compartilhe este código."

    try:
        result = subprocess.run(
            [
                OPENCLAW_BIN, "message", "send",
                "--channel", "whatsapp",
                "--target", phone,
                "--message", message,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            logger.info(f"OTP enviado via WhatsApp para {phone}")
            return True
        else:
            logger.error(f"Falha ao enviar OTP via WhatsApp para {phone}: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout ao enviar OTP via WhatsApp para {phone}")
        return False
    except FileNotFoundError:
        logger.error(f"OpenClaw CLI não encontrado em {OPENCLAW_BIN}")
        return False
    except Exception as e:
        logger.error(f"Erro ao enviar OTP via WhatsApp: {e}")
        return False
