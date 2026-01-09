"""
Tenant Service - Serviço de configuração White Label

Fornece acesso às configurações do tenant:
- Brand config (nome, cores, logo)
- Diagnosis areas (áreas de diagnóstico)
- Agent configs (prompts de agentes)
- Feature flags (CRM, diagnóstico, chat)

Cache em memória com TTL para performance.
"""

import os
import json
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import sqlite3

logger = logging.getLogger(__name__)

# Path do banco
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'crm.db')

# Cache TTL (5 minutos)
CACHE_TTL = timedelta(minutes=5)


@dataclass
class BrandConfig:
    """Configuração de marca/identidade visual."""
    tenant_id: str = "default"
    brand_name: str = "Nanda"
    brand_tagline: str = "sua mentora"
    brand_description: str = "Sistema de Diagnóstico Inteligente"
    primary_color: str = "#059669"
    primary_light: str = "#d1fae5"
    primary_dark: str = "#047857"
    secondary_color: str = "#10b981"
    logo_url: Optional[str] = None
    favicon_url: Optional[str] = None
    api_domain: str = "localhost:8234"
    web_domain: str = "localhost:4200"

    # Contexto Agnóstico - configurável por tenant
    target_audience: str = "profissionais e empresários"
    business_context: str = "ajudar profissionais e empresários a crescerem seus negócios"
    client_term: str = "cliente"
    client_term_plural: str = "clientes"
    service_term: str = "serviço"
    team_term: str = "equipe"
    audience_goals: List[str] = field(default_factory=lambda: [
        "Melhorar seu posicionamento de mercado",
        "Aumentar sua precificação e faturamento",
        "Desenvolver estratégias de vendas",
        "Criar estratégias de atração e fidelização",
        "Otimizar a experiência do cliente",
        "Gerenciar melhor sua equipe"
    ])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.brand_name,
            "tagline": self.brand_tagline,
            "description": self.brand_description,
            "primaryColor": self.primary_color,
            "primaryLight": self.primary_light,
            "primaryDark": self.primary_dark,
            "secondaryColor": self.secondary_color,
            "logoUrl": self.logo_url,
            "faviconUrl": self.favicon_url,
            "apiDomain": self.api_domain,
            "webDomain": self.web_domain,
            # Contexto Agnóstico
            "targetAudience": self.target_audience,
            "businessContext": self.business_context,
            "clientTerm": self.client_term,
            "clientTermPlural": self.client_term_plural,
            "serviceTerm": self.service_term,
            "teamTerm": self.team_term,
            "audienceGoals": self.audience_goals,
        }


@dataclass
class DiagnosisArea:
    """Área de diagnóstico configurável."""
    area_id: int
    area_key: str
    area_name: str
    description: Optional[str] = None
    area_icon: str = "📊"
    display_order: int = 0
    is_active: bool = True
    tenant_id: str = "default"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.area_id,
            "key": self.area_key,
            "name": self.area_name,
            "description": self.description,
            "icon": self.area_icon,
            "order": self.display_order,
            "isActive": self.is_active,
        }


@dataclass
class AgentConfig:
    """Configuração de agente/prompt."""
    id: int
    agent_key: str
    agent_name: str
    agent_description: Optional[str] = None
    system_prompt: str = ""
    model: str = "claude-sonnet-4-20250514"
    temperature: float = 0.7
    max_tokens: int = 4096
    allowed_tools: List[str] = field(default_factory=list)
    allowed_roles: List[str] = field(default_factory=lambda: ["admin"])
    is_active: bool = True
    tenant_id: str = "default"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "key": self.agent_key,
            "name": self.agent_name,
            "description": self.agent_description,
            "systemPrompt": self.system_prompt,
            "model": self.model,
            "temperature": self.temperature,
            "maxTokens": self.max_tokens,
            "allowedTools": self.allowed_tools,
            "allowedRoles": self.allowed_roles,
            "isActive": self.is_active,
        }


@dataclass
class TenantConfig:
    """Configuração completa do tenant."""
    tenant_id: str = "default"
    brand: BrandConfig = field(default_factory=BrandConfig)
    crm_enabled: bool = True
    diagnosis_enabled: bool = True
    chat_enabled: bool = True


class TenantService:
    """
    Serviço para gerenciar configurações de tenant (White Label).

    Usa cache em memória com TTL para evitar queries frequentes.
    """

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._cache: Dict[str, Any] = {}
        self._cache_times: Dict[str, datetime] = {}
        logger.info(f"TenantService initialized with db: {db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """Obtém conexão com o banco."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _is_cache_valid(self, key: str) -> bool:
        """Verifica se o cache ainda é válido."""
        if key not in self._cache_times:
            return False
        return datetime.now() - self._cache_times[key] < CACHE_TTL

    def _set_cache(self, key: str, value: Any) -> None:
        """Armazena valor no cache."""
        self._cache[key] = value
        self._cache_times[key] = datetime.now()

    def _get_cache(self, key: str) -> Optional[Any]:
        """Obtém valor do cache se válido."""
        if self._is_cache_valid(key):
            return self._cache.get(key)
        return None

    def clear_cache(self, tenant_id: str = "default") -> None:
        """Limpa cache de um tenant específico."""
        keys_to_remove = [k for k in self._cache if k.startswith(f"{tenant_id}:")]
        for key in keys_to_remove:
            self._cache.pop(key, None)
            self._cache_times.pop(key, None)
        logger.info(f"Cache cleared for tenant: {tenant_id}")

    def get_brand(self, tenant_id: str = "default") -> BrandConfig:
        """Obtém configuração de marca do tenant."""
        cache_key = f"{tenant_id}:brand"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT * FROM tenant_config WHERE tenant_id = ?
            """, (tenant_id,))
            row = cursor.fetchone()

            if row:
                # Parse audience_goals (JSON array)
                audience_goals_raw = row["audience_goals"] if "audience_goals" in row.keys() else None
                if audience_goals_raw:
                    try:
                        audience_goals = json.loads(audience_goals_raw)
                    except json.JSONDecodeError:
                        audience_goals = BrandConfig.audience_goals
                else:
                    audience_goals = [
                        "Melhorar seu posicionamento de mercado",
                        "Aumentar sua precificação e faturamento",
                        "Desenvolver estratégias de vendas",
                        "Criar estratégias de atração e fidelização",
                        "Otimizar a experiência do cliente",
                        "Gerenciar melhor sua equipe"
                    ]

                brand = BrandConfig(
                    tenant_id=row["tenant_id"],
                    brand_name=row["brand_name"],
                    brand_tagline=row["brand_tagline"] or "sua mentora",
                    brand_description=row["brand_description"] or "",
                    primary_color=row["primary_color"] or "#059669",
                    primary_light=row["primary_light"] or "#d1fae5",
                    primary_dark=row["primary_dark"] or "#047857",
                    secondary_color=row["secondary_color"] or "#10b981",
                    logo_url=row["logo_url"],
                    favicon_url=row["favicon_url"],
                    api_domain=row["api_domain"] or "localhost:8234",
                    web_domain=row["web_domain"] or "localhost:4200",
                    # Contexto Agnóstico
                    target_audience=row["target_audience"] if "target_audience" in row.keys() else "profissionais e empresários",
                    business_context=row["business_context"] if "business_context" in row.keys() else "ajudar profissionais e empresários a crescerem seus negócios",
                    client_term=row["client_term"] if "client_term" in row.keys() else "cliente",
                    client_term_plural=row["client_term_plural"] if "client_term_plural" in row.keys() else "clientes",
                    service_term=row["service_term"] if "service_term" in row.keys() else "serviço",
                    team_term=row["team_term"] if "team_term" in row.keys() else "equipe",
                    audience_goals=audience_goals,
                )
            else:
                brand = BrandConfig(tenant_id=tenant_id)
                logger.warning(f"No config found for tenant {tenant_id}, using defaults")

            self._set_cache(cache_key, brand)
            return brand

        finally:
            conn.close()

    def get_diagnosis_areas(self, tenant_id: str = "default", active_only: bool = True) -> List[DiagnosisArea]:
        """Obtém áreas de diagnóstico do tenant."""
        cache_key = f"{tenant_id}:areas:{active_only}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        conn = self._get_connection()
        try:
            query = """
                SELECT area_id, area_key, area_name, description,
                       COALESCE(area_icon, '📊') as area_icon,
                       COALESCE(order_index, 0) as display_order,
                       COALESCE(is_active, 1) as is_active,
                       COALESCE(tenant_id, 'default') as tenant_id
                FROM diagnosis_areas
                WHERE COALESCE(tenant_id, 'default') = ?
            """
            if active_only:
                query += " AND COALESCE(is_active, 1) = 1"
            query += " ORDER BY COALESCE(order_index, 0)"

            cursor = conn.execute(query, (tenant_id,))
            areas = []
            for row in cursor.fetchall():
                areas.append(DiagnosisArea(
                    area_id=row["area_id"],
                    area_key=row["area_key"],
                    area_name=row["area_name"],
                    description=row["description"],
                    area_icon=row["area_icon"],
                    display_order=row["display_order"],
                    is_active=bool(row["is_active"]),
                    tenant_id=row["tenant_id"],
                ))

            self._set_cache(cache_key, areas)
            return areas

        finally:
            conn.close()

    def get_agent_config(self, agent_key: str, tenant_id: str = "default") -> Optional[AgentConfig]:
        """Obtém configuração de um agente específico."""
        cache_key = f"{tenant_id}:agent:{agent_key}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT * FROM agent_configs
                WHERE agent_key = ? AND tenant_id = ? AND is_active = 1
            """, (agent_key, tenant_id))
            row = cursor.fetchone()

            if not row:
                logger.warning(f"Agent {agent_key} not found for tenant {tenant_id}")
                return None

            # Parse JSON fields
            allowed_tools = json.loads(row["allowed_tools"]) if row["allowed_tools"] else []
            allowed_roles = json.loads(row["allowed_roles"]) if row["allowed_roles"] else ["admin"]

            agent = AgentConfig(
                id=row["id"],
                agent_key=row["agent_key"],
                agent_name=row["agent_name"],
                agent_description=row["agent_description"],
                system_prompt=row["system_prompt"],
                model=row["model"] or "claude-sonnet-4-20250514",
                temperature=row["temperature"] or 0.7,
                max_tokens=row["max_tokens"] or 4096,
                allowed_tools=allowed_tools,
                allowed_roles=allowed_roles,
                is_active=bool(row["is_active"]),
                tenant_id=row["tenant_id"],
            )

            self._set_cache(cache_key, agent)
            return agent

        finally:
            conn.close()

    def get_all_agents(self, tenant_id: str = "default", role: Optional[str] = None) -> List[AgentConfig]:
        """Obtém todos os agentes ativos do tenant, filtrados por role se especificado."""
        cache_key = f"{tenant_id}:agents:{role}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT * FROM agent_configs
                WHERE tenant_id = ? AND is_active = 1
                ORDER BY agent_name
            """, (tenant_id,))

            agents = []
            for row in cursor.fetchall():
                allowed_tools = json.loads(row["allowed_tools"]) if row["allowed_tools"] else []
                allowed_roles = json.loads(row["allowed_roles"]) if row["allowed_roles"] else ["admin"]

                # Filtrar por role se especificado
                if role and role not in allowed_roles:
                    continue

                agents.append(AgentConfig(
                    id=row["id"],
                    agent_key=row["agent_key"],
                    agent_name=row["agent_name"],
                    agent_description=row["agent_description"],
                    system_prompt=row["system_prompt"],
                    model=row["model"] or "claude-sonnet-4-20250514",
                    temperature=row["temperature"] or 0.7,
                    max_tokens=row["max_tokens"] or 4096,
                    allowed_tools=allowed_tools,
                    allowed_roles=allowed_roles,
                    is_active=bool(row["is_active"]),
                    tenant_id=row["tenant_id"],
                ))

            self._set_cache(cache_key, agents)
            return agents

        finally:
            conn.close()

    def is_feature_enabled(self, feature: str, tenant_id: str = "default") -> bool:
        """Verifica se uma feature está habilitada para o tenant."""
        cache_key = f"{tenant_id}:feature:{feature}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        conn = self._get_connection()
        try:
            cursor = conn.execute(f"""
                SELECT {feature}_enabled FROM tenant_config WHERE tenant_id = ?
            """, (tenant_id,))
            row = cursor.fetchone()

            enabled = bool(row[0]) if row else True
            self._set_cache(cache_key, enabled)
            return enabled

        except Exception as e:
            logger.error(f"Error checking feature {feature}: {e}")
            return True  # Default: habilitado

        finally:
            conn.close()

    def get_config(self, tenant_id: str = "default") -> TenantConfig:
        """Obtém configuração completa do tenant."""
        brand = self.get_brand(tenant_id)
        return TenantConfig(
            tenant_id=tenant_id,
            brand=brand,
            crm_enabled=self.is_feature_enabled("crm", tenant_id),
            diagnosis_enabled=self.is_feature_enabled("diagnosis", tenant_id),
            chat_enabled=self.is_feature_enabled("chat", tenant_id),
        )

    def render_prompt(self, prompt_template: str, tenant_id: str = "default") -> str:
        """
        Renderiza um template de prompt substituindo variáveis.

        Variáveis suportadas:
        - {brand_name}: Nome da marca
        - {brand_tagline}: Tagline
        - {diagnosis_areas}: Lista formatada das áreas de diagnóstico
        """
        brand = self.get_brand(tenant_id)
        areas = self.get_diagnosis_areas(tenant_id)

        # Formatar áreas de diagnóstico
        areas_text = "\n".join([
            f"- {area.area_icon} {area.area_name}: {area.description or 'Sem descrição'}"
            for area in areas
        ])

        return prompt_template.format(
            brand_name=brand.brand_name,
            brand_tagline=brand.brand_tagline,
            diagnosis_areas=areas_text,
        )

    # =========================================================
    # MÉTODOS DE HIERARQUIA DE TENANT (Multi-tenant)
    # =========================================================

    def get_tenant_hierarchy(self, tenant_id: str = "default") -> Dict[str, Any]:
        """Obtém informações de hierarquia do tenant."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT tenant_id, brand_name, parent_tenant_id, owner_user_id, tenant_type
                FROM tenant_config WHERE tenant_id = ?
            """, (tenant_id,))
            row = cursor.fetchone()

            if not row:
                return {
                    "tenantId": tenant_id,
                    "brandName": "Default",
                    "parentTenantId": None,
                    "ownerUserId": None,
                    "tenantType": "root",
                    "childTenants": [],
                }

            # Buscar tenants filhos
            cursor = conn.execute("""
                SELECT tenant_id, brand_name, owner_user_id
                FROM tenant_config WHERE parent_tenant_id = ?
            """, (tenant_id,))
            children = []
            for child in cursor.fetchall():
                children.append({
                    "tenantId": child["tenant_id"],
                    "brandName": child["brand_name"],
                    "ownerUserId": child["owner_user_id"],
                })

            return {
                "tenantId": row["tenant_id"],
                "brandName": row["brand_name"],
                "parentTenantId": row["parent_tenant_id"],
                "ownerUserId": row["owner_user_id"],
                "tenantType": row["tenant_type"] or "root",
                "childTenants": children,
            }

        finally:
            conn.close()

    def get_child_tenants(self, parent_tenant_id: str = "default") -> List[Dict[str, Any]]:
        """Lista todos os tenants filhos de um tenant pai."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT tc.tenant_id, tc.brand_name, tc.owner_user_id,
                       u.username as owner_name, u.email as owner_email
                FROM tenant_config tc
                LEFT JOIN users u ON tc.owner_user_id = u.user_id
                WHERE tc.parent_tenant_id = ?
                ORDER BY tc.brand_name
            """, (parent_tenant_id,))

            children = []
            for row in cursor.fetchall():
                children.append({
                    "tenantId": row["tenant_id"],
                    "brandName": row["brand_name"],
                    "ownerUserId": row["owner_user_id"],
                    "ownerName": row["owner_name"],
                    "ownerEmail": row["owner_email"],
                })

            return children

        finally:
            conn.close()

    def is_tenant_owner(self, user_id: int, tenant_id: str) -> bool:
        """Verifica se um usuário é owner de um tenant."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT 1 FROM tenant_config
                WHERE tenant_id = ? AND owner_user_id = ?
            """, (tenant_id, user_id))
            return cursor.fetchone() is not None

        finally:
            conn.close()


# Instância global (singleton)
_tenant_service: Optional[TenantService] = None


def get_tenant_service() -> TenantService:
    """Obtém instância global do TenantService."""
    global _tenant_service
    if _tenant_service is None:
        _tenant_service = TenantService()
    return _tenant_service
