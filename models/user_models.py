"""
User Models - Validações Pydantic para usuários do sistema

Define schemas para:
- Criação de mentorados (via phone_number, passwordless)
- Atualização de perfil
- Validações de dados

Uso: Importar nas rotas para validar payloads
"""

from typing import Optional
from pydantic import BaseModel, Field, field_validator
import re


class MentoradoCreate(BaseModel):
    """Schema para criação de mentorado (passwordless — via WhatsApp OTP)"""

    username: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Nome completo do mentorado"
    )
    phone_number: str = Field(
        ...,
        description="Telefone no formato +5511999999999 ou 11999999999"
    )
    email: Optional[str] = Field(
        None,
        description="Email opcional (informativo)"
    )
    profession: Optional[str] = Field(
        None,
        max_length=100,
        description="Profissão do mentorado"
    )
    specialty: Optional[str] = Field(
        None,
        max_length=100,
        description="Especialidade ou nicho de atuação"
    )
    current_revenue: Optional[float] = Field(
        None,
        ge=0,
        description="Faturamento atual em reais"
    )
    desired_revenue: Optional[float] = Field(
        None,
        ge=0,
        description="Faturamento desejado em reais"
    )

    @field_validator('phone_number')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        # Remove caracteres não numéricos exceto +
        cleaned = re.sub(r'[^\d+]', '', v)
        # Valida formato brasileiro
        if not re.match(r'^\+?[0-9]{10,15}$', cleaned):
            raise ValueError('Telefone inválido. Use formato: +5511999999999 ou 11999999999')
        return cleaned

    @field_validator('desired_revenue')
    @classmethod
    def validate_desired_revenue(cls, v: Optional[float], info) -> Optional[float]:
        if v is None:
            return v
        current = info.data.get('current_revenue')
        if current is not None and v < current:
            raise ValueError('Faturamento desejado deve ser maior ou igual ao atual')
        return v


class MentoradoUpdate(BaseModel):
    """Schema para atualização de mentorado"""

    username: Optional[str] = Field(
        None,
        min_length=2,
        max_length=100
    )
    phone_number: Optional[str] = None
    profession: Optional[str] = Field(None, max_length=100)
    specialty: Optional[str] = Field(None, max_length=100)
    current_revenue: Optional[float] = Field(None, ge=0)
    desired_revenue: Optional[float] = Field(None, ge=0)
    profile_image_url: Optional[str] = None

    @field_validator('phone_number')
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        cleaned = re.sub(r'[^\d+]', '', v)
        if not re.match(r'^\+?[0-9]{10,15}$', cleaned):
            raise ValueError('Telefone inválido')
        return cleaned


class MentoradoResponse(BaseModel):
    """Schema de resposta para mentorado"""

    user_id: int
    username: str
    email: Optional[str] = None
    phone_number: Optional[str] = None
    profession: Optional[str] = None
    specialty: Optional[str] = None
    current_revenue: Optional[float] = None
    desired_revenue: Optional[float] = None
    profile_image_url: Optional[str] = None
    account_status: str = 'active'
    registration_date: Optional[str] = None

    class Config:
        from_attributes = True


class AdminCreate(BaseModel):
    """Schema para criação de admin (passwordless)"""

    username: str = Field(..., min_length=2, max_length=100)
    phone_number: str = Field(..., description="Telefone E.164")

    @field_validator('phone_number')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        cleaned = re.sub(r'[^\d+]', '', v)
        if not re.match(r'^\+?[0-9]{10,15}$', cleaned):
            raise ValueError('Telefone inválido')
        return cleaned
