"""
Evolution Service - Serviço de Evolução de Usuários

Gerencia o fluxo de evolução (flywheel) agnóstico:
- Lead → Recebe Valor → Troca Valor → Gera Valor
- Promoção de usuários entre estágios
- Criação de tenant para estágios que geram valor

Compatível com qualquer modelo de negócio.
"""

import os
import json
import logging
import uuid
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
import sqlite3

logger = logging.getLogger(__name__)

# Path do banco
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'crm.db')


@dataclass
class EvolutionStage:
    """Representa um estágio de evolução."""
    id: int
    tenant_id: str
    stage_key: str
    stage_name: str
    stage_level: int
    stage_type: str  # 'lead', 'receives_value', 'trades_value', 'generates_value'
    description: Optional[str] = None
    creates_tenant: bool = False
    permissions: List[str] = field(default_factory=list)
    is_active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "key": self.stage_key,
            "name": self.stage_name,
            "level": self.stage_level,
            "type": self.stage_type,
            "description": self.description,
            "createsTenant": self.creates_tenant,
            "permissions": self.permissions,
            "isActive": self.is_active,
        }


@dataclass
class PromotionResult:
    """Resultado de uma promoção de usuário."""
    success: bool
    user_id: int
    from_stage: str
    to_stage: str
    new_tenant_id: Optional[str] = None
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "userId": self.user_id,
            "fromStage": self.from_stage,
            "toStage": self.to_stage,
            "newTenantId": self.new_tenant_id,
            "message": self.message,
        }


class EvolutionService:
    """
    Serviço para gerenciar evolução de usuários.

    Flywheel Agnóstico:
    - Lead: Novo contato
    - Receives Value: Recebe valor (mentorado, aluno, trial)
    - Trades Value: Troca valor (cliente pagante)
    - Generates Value: Gera valor (mentor, afiliado, multiplicador)
    """

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        logger.info(f"EvolutionService initialized with db: {db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """Obtém conexão com o banco."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # =========================================================
    # STAGES - Consulta e Configuração
    # =========================================================

    def get_stages(self, tenant_id: str = "default", active_only: bool = True) -> List[EvolutionStage]:
        """Obtém estágios de evolução do tenant."""
        conn = self._get_connection()
        try:
            query = """
                SELECT * FROM evolution_stages
                WHERE tenant_id = ?
            """
            if active_only:
                query += " AND is_active = 1"
            query += " ORDER BY stage_level"

            cursor = conn.execute(query, (tenant_id,))
            stages = []

            for row in cursor.fetchall():
                permissions = []
                if row["permissions"]:
                    try:
                        permissions = json.loads(row["permissions"])
                    except json.JSONDecodeError:
                        permissions = []

                stages.append(EvolutionStage(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    stage_key=row["stage_key"],
                    stage_name=row["stage_name"],
                    stage_level=row["stage_level"],
                    stage_type=row["stage_type"],
                    description=row["description"],
                    creates_tenant=bool(row["creates_tenant"]),
                    permissions=permissions,
                    is_active=bool(row["is_active"]),
                ))

            return stages

        finally:
            conn.close()

    def get_stage(self, stage_key: str, tenant_id: str = "default") -> Optional[EvolutionStage]:
        """Obtém um estágio específico."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT * FROM evolution_stages
                WHERE tenant_id = ? AND stage_key = ?
            """, (tenant_id, stage_key))
            row = cursor.fetchone()

            if not row:
                return None

            permissions = []
            if row["permissions"]:
                try:
                    permissions = json.loads(row["permissions"])
                except json.JSONDecodeError:
                    permissions = []

            return EvolutionStage(
                id=row["id"],
                tenant_id=row["tenant_id"],
                stage_key=row["stage_key"],
                stage_name=row["stage_name"],
                stage_level=row["stage_level"],
                stage_type=row["stage_type"],
                description=row["description"],
                creates_tenant=bool(row["creates_tenant"]),
                permissions=permissions,
                is_active=bool(row["is_active"]),
            )

        finally:
            conn.close()

    def update_stage(
        self,
        stage_key: str,
        tenant_id: str = "default",
        stage_name: Optional[str] = None,
        description: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        creates_tenant: Optional[bool] = None,
    ) -> bool:
        """Atualiza configuração de um estágio."""
        conn = self._get_connection()
        try:
            updates = []
            params = []

            if stage_name is not None:
                updates.append("stage_name = ?")
                params.append(stage_name)

            if description is not None:
                updates.append("description = ?")
                params.append(description)

            if permissions is not None:
                updates.append("permissions = ?")
                params.append(json.dumps(permissions))

            if creates_tenant is not None:
                updates.append("creates_tenant = ?")
                params.append(creates_tenant)

            if not updates:
                return False

            updates.append("updated_at = datetime('now')")
            params.extend([tenant_id, stage_key])

            query = f"""
                UPDATE evolution_stages
                SET {', '.join(updates)}
                WHERE tenant_id = ? AND stage_key = ?
            """

            conn.execute(query, params)
            conn.commit()

            logger.info(f"Stage {stage_key} updated for tenant {tenant_id}")
            return True

        finally:
            conn.close()

    def add_stage(
        self,
        tenant_id: str,
        stage_key: str,
        stage_name: str,
        stage_level: int,
        stage_type: str,
        description: Optional[str] = None,
        creates_tenant: bool = False,
        permissions: Optional[List[str]] = None,
    ) -> bool:
        """Adiciona novo estágio ao tenant."""
        conn = self._get_connection()
        try:
            conn.execute("""
                INSERT INTO evolution_stages
                (tenant_id, stage_key, stage_name, stage_level, stage_type,
                 description, creates_tenant, permissions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tenant_id,
                stage_key,
                stage_name,
                stage_level,
                stage_type,
                description,
                creates_tenant,
                json.dumps(permissions or []),
            ))
            conn.commit()

            logger.info(f"Stage {stage_key} added for tenant {tenant_id}")
            return True

        except sqlite3.IntegrityError:
            logger.warning(f"Stage {stage_key} already exists for tenant {tenant_id}")
            return False

        finally:
            conn.close()

    def delete_stage(self, stage_key: str, tenant_id: str = "default") -> bool:
        """Remove um estágio do tenant."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                DELETE FROM evolution_stages
                WHERE tenant_id = ? AND stage_key = ?
            """, (tenant_id, stage_key))
            conn.commit()

            if cursor.rowcount > 0:
                logger.info(f"Stage {stage_key} deleted for tenant {tenant_id}")
                return True
            return False

        finally:
            conn.close()

    # =========================================================
    # PROMOÇÃO DE USUÁRIOS
    # =========================================================

    def get_user_stage(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Obtém informações do estágio atual do usuário."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT u.user_id, u.username, u.email, u.role,
                       u.tenant_id, u.current_stage_key, u.stage_history,
                       u.promoted_to_tenant_id,
                       es.stage_name, es.stage_level, es.stage_type
                FROM users u
                LEFT JOIN evolution_stages es
                    ON u.tenant_id = es.tenant_id AND u.current_stage_key = es.stage_key
                WHERE u.user_id = ?
            """, (user_id,))
            row = cursor.fetchone()

            if not row:
                return None

            stage_history = []
            if row["stage_history"]:
                try:
                    stage_history = json.loads(row["stage_history"])
                except json.JSONDecodeError:
                    stage_history = []

            return {
                "userId": row["user_id"],
                "username": row["username"],
                "email": row["email"],
                "role": row["role"],
                "tenantId": row["tenant_id"] or "default",
                "currentStageKey": row["current_stage_key"] or "lead",
                "stageName": row["stage_name"] or "Lead",
                "stageLevel": row["stage_level"] or 0,
                "stageType": row["stage_type"] or "lead",
                "stageHistory": stage_history,
                "promotedToTenantId": row["promoted_to_tenant_id"],
            }

        finally:
            conn.close()

    def promote_user(
        self,
        user_id: int,
        to_stage_key: str,
        promoted_by: int,
        tenant_id: str = "default",
    ) -> PromotionResult:
        """
        Promove usuário para um novo estágio.

        Se o estágio destino tem creates_tenant=True, cria novo tenant.
        """
        conn = self._get_connection()
        try:
            # 1. Obter usuário atual
            cursor = conn.execute("""
                SELECT user_id, username, email, current_stage_key, tenant_id, stage_history
                FROM users WHERE user_id = ?
            """, (user_id,))
            user = cursor.fetchone()

            if not user:
                return PromotionResult(
                    success=False,
                    user_id=user_id,
                    from_stage="",
                    to_stage=to_stage_key,
                    message="Usuário não encontrado"
                )

            from_stage_key = user["current_stage_key"] or "lead"
            current_tenant = user["tenant_id"] or "default"

            # 2. Obter estágios
            from_stage = self.get_stage(from_stage_key, current_tenant)
            to_stage = self.get_stage(to_stage_key, tenant_id)

            if not to_stage:
                return PromotionResult(
                    success=False,
                    user_id=user_id,
                    from_stage=from_stage_key,
                    to_stage=to_stage_key,
                    message=f"Estágio '{to_stage_key}' não encontrado"
                )

            # 3. Validar promoção (nível deve ser maior ou igual)
            from_level = from_stage.stage_level if from_stage else 0
            if to_stage.stage_level < from_level:
                return PromotionResult(
                    success=False,
                    user_id=user_id,
                    from_stage=from_stage_key,
                    to_stage=to_stage_key,
                    message=f"Não é possível rebaixar usuário de nível {from_level} para {to_stage.stage_level}"
                )

            # 4. Se estágio cria tenant, criar novo tenant
            new_tenant_id = None
            if to_stage.creates_tenant:
                new_tenant_id = self._create_child_tenant(
                    parent_tenant_id=current_tenant,
                    owner_user_id=user_id,
                    owner_username=user["username"],
                )
                logger.info(f"Created new tenant {new_tenant_id} for user {user_id}")

            # 5. Atualizar histórico
            stage_history = []
            if user["stage_history"]:
                try:
                    stage_history = json.loads(user["stage_history"])
                except json.JSONDecodeError:
                    stage_history = []

            stage_history.append({
                "from": from_stage_key,
                "to": to_stage_key,
                "promotedBy": promoted_by,
                "promotedAt": datetime.now().isoformat(),
                "newTenantId": new_tenant_id,
            })

            # 6. Atualizar usuário
            conn.execute("""
                UPDATE users
                SET current_stage_key = ?,
                    stage_history = ?,
                    promoted_to_tenant_id = COALESCE(?, promoted_to_tenant_id)
                WHERE user_id = ?
            """, (
                to_stage_key,
                json.dumps(stage_history),
                new_tenant_id,
                user_id,
            ))

            # 7. Se criou tenant, atualizar role para admin do novo tenant
            if new_tenant_id:
                conn.execute("""
                    UPDATE users
                    SET role = 'admin'
                    WHERE user_id = ?
                """, (user_id,))

            conn.commit()

            message = f"Promovido de {from_stage_key} para {to_stage_key}"
            if new_tenant_id:
                message += f". Novo tenant criado: {new_tenant_id}"

            logger.info(f"User {user_id} promoted: {from_stage_key} → {to_stage_key}")

            return PromotionResult(
                success=True,
                user_id=user_id,
                from_stage=from_stage_key,
                to_stage=to_stage_key,
                new_tenant_id=new_tenant_id,
                message=message,
            )

        except Exception as e:
            logger.error(f"Error promoting user {user_id}: {e}")
            return PromotionResult(
                success=False,
                user_id=user_id,
                from_stage="",
                to_stage=to_stage_key,
                message=str(e)
            )

        finally:
            conn.close()

    def _create_child_tenant(
        self,
        parent_tenant_id: str,
        owner_user_id: int,
        owner_username: str,
    ) -> str:
        """Cria tenant filho para usuário promovido."""
        conn = self._get_connection()
        try:
            # Gerar ID único para o tenant
            new_tenant_id = f"tenant_{owner_user_id}_{uuid.uuid4().hex[:8]}"

            # Copiar configuração do tenant pai
            cursor = conn.execute("""
                SELECT * FROM tenant_config WHERE tenant_id = ?
            """, (parent_tenant_id,))
            parent_config = cursor.fetchone()

            if parent_config:
                # Inserir novo tenant baseado no pai
                conn.execute("""
                    INSERT INTO tenant_config (
                        tenant_id, brand_name, brand_tagline, brand_description,
                        primary_color, primary_light, primary_dark, secondary_color,
                        logo_url, favicon_url, api_domain, web_domain,
                        crm_enabled, diagnosis_enabled, chat_enabled,
                        target_audience, business_context, client_term,
                        client_term_plural, service_term, team_term, audience_goals,
                        parent_tenant_id, owner_user_id, tenant_type
                    ) VALUES (
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?
                    )
                """, (
                    new_tenant_id,
                    owner_username,  # brand_name = nome do owner
                    parent_config["brand_tagline"],
                    parent_config["brand_description"],
                    parent_config["primary_color"],
                    parent_config["primary_light"],
                    parent_config["primary_dark"],
                    parent_config["secondary_color"],
                    None,  # logo_url - vazio
                    None,  # favicon_url - vazio
                    parent_config["api_domain"],
                    parent_config["web_domain"],
                    parent_config["crm_enabled"],
                    parent_config["diagnosis_enabled"],
                    parent_config["chat_enabled"],
                    parent_config["target_audience"],
                    parent_config["business_context"],
                    parent_config["client_term"],
                    parent_config["client_term_plural"],
                    parent_config["service_term"],
                    parent_config["team_term"],
                    parent_config["audience_goals"],
                    parent_tenant_id,  # parent_tenant_id
                    owner_user_id,  # owner_user_id
                    "child",  # tenant_type
                ))

            # Copiar estágios do tenant pai
            cursor = conn.execute("""
                SELECT * FROM evolution_stages WHERE tenant_id = ?
            """, (parent_tenant_id,))
            parent_stages = cursor.fetchall()

            for stage in parent_stages:
                conn.execute("""
                    INSERT INTO evolution_stages (
                        tenant_id, stage_key, stage_name, stage_level, stage_type,
                        description, creates_tenant, permissions, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    new_tenant_id,
                    stage["stage_key"],
                    stage["stage_name"],
                    stage["stage_level"],
                    stage["stage_type"],
                    stage["description"],
                    stage["creates_tenant"],
                    stage["permissions"],
                    stage["is_active"],
                ))

            conn.commit()
            return new_tenant_id

        finally:
            conn.close()

    # =========================================================
    # CONSULTAS
    # =========================================================

    def get_users_by_stage(
        self,
        stage_key: str,
        tenant_id: str = "default",
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Lista usuários em um determinado estágio."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT u.user_id, u.username, u.email, u.phone_number,
                       u.profession, u.current_stage_key, u.registration_date
                FROM users u
                WHERE u.tenant_id = ? AND u.current_stage_key = ?
                AND u.deleted_at IS NULL
                ORDER BY u.registration_date DESC
                LIMIT ? OFFSET ?
            """, (tenant_id, stage_key, limit, offset))

            users = []
            for row in cursor.fetchall():
                users.append({
                    "userId": row["user_id"],
                    "username": row["username"],
                    "email": row["email"],
                    "phone": row["phone_number"],
                    "profession": row["profession"],
                    "stageKey": row["current_stage_key"],
                    "registeredAt": row["registration_date"],
                })

            return users

        finally:
            conn.close()

    def get_evolution_funnel(self, tenant_id: str = "default") -> List[Dict[str, Any]]:
        """Obtém funil de evolução com contagens por estágio."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT
                    es.stage_key,
                    es.stage_name,
                    es.stage_level,
                    es.stage_type,
                    COUNT(u.user_id) as user_count
                FROM evolution_stages es
                LEFT JOIN users u ON u.tenant_id = es.tenant_id
                    AND u.current_stage_key = es.stage_key
                    AND u.deleted_at IS NULL
                WHERE es.tenant_id = ? AND es.is_active = 1
                GROUP BY es.stage_key
                ORDER BY es.stage_level
            """, (tenant_id,))

            funnel = []
            for row in cursor.fetchall():
                funnel.append({
                    "stageKey": row["stage_key"],
                    "stageName": row["stage_name"],
                    "stageLevel": row["stage_level"],
                    "stageType": row["stage_type"],
                    "userCount": row["user_count"],
                })

            return funnel

        finally:
            conn.close()


# Instância global (singleton)
_evolution_service: Optional[EvolutionService] = None


def get_evolution_service() -> EvolutionService:
    """Obtém instância global do EvolutionService."""
    global _evolution_service
    if _evolution_service is None:
        _evolution_service = EvolutionService()
    return _evolution_service
