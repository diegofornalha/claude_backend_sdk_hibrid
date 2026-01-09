"""
Admin Level Service - Serviço de Níveis Hierárquicos de Gestão

Gerencia a hierarquia administrativa agnóstica:
- Nível 1 = Dono Master (controle total)
- Nível 2, 3, 4... = Níveis configuráveis de gestão

Separado do Flywheel (que é para jornada do cliente).
"""

import os
import json
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
import sqlite3

logger = logging.getLogger(__name__)

# Path do banco
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'crm.db')


@dataclass
class AdminLevel:
    """Representa um nível hierárquico de gestão."""
    id: int
    tenant_id: str
    level: int  # 1 = Dono, 2 = Diretor, 3 = Gestor, etc
    name: str
    description: Optional[str] = None
    permissions: List[str] = field(default_factory=list)
    can_manage_levels: List[int] = field(default_factory=list)
    is_active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "name": self.name,
            "description": self.description,
            "permissions": self.permissions,
            "canManageLevels": self.can_manage_levels,
            "isActive": self.is_active,
        }


class AdminLevelService:
    """
    Serviço para gerenciar níveis hierárquicos de gestão.

    Hierarquia Agnóstica:
    - Nível 1: Dono Master (controle total)
    - Nível 2: Segundo na hierarquia (ex: Diretor)
    - Nível 3: Terceiro (ex: Gestor)
    - Nível N: Configurável

    Regras:
    - Nível menor = mais poder
    - Nível N pode gerenciar níveis > N
    """

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        logger.info(f"AdminLevelService initialized with db: {db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """Obtém conexão com o banco."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _parse_can_manage_levels(self, value: Optional[str]) -> List[int]:
        """Converte string '2,3,4' para lista de inteiros."""
        if not value:
            return []
        try:
            return [int(x.strip()) for x in value.split(",") if x.strip()]
        except (ValueError, AttributeError):
            return []

    def _serialize_can_manage_levels(self, levels: List[int]) -> str:
        """Converte lista de inteiros para string '2,3,4'."""
        return ",".join(str(x) for x in sorted(levels))

    # =========================================================
    # LEVELS - Consulta e Configuração
    # =========================================================

    def get_levels(self, tenant_id: str = "default", active_only: bool = True) -> List[AdminLevel]:
        """Obtém níveis hierárquicos do tenant."""
        conn = self._get_connection()
        try:
            query = """
                SELECT * FROM admin_levels
                WHERE tenant_id = ?
            """
            if active_only:
                query += " AND is_active = 1"
            query += " ORDER BY level"

            cursor = conn.execute(query, (tenant_id,))
            levels = []

            for row in cursor.fetchall():
                permissions = []
                if row["permissions"]:
                    try:
                        permissions = json.loads(row["permissions"])
                    except json.JSONDecodeError:
                        permissions = []

                levels.append(AdminLevel(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    level=row["level"],
                    name=row["name"],
                    description=row["description"],
                    permissions=permissions,
                    can_manage_levels=self._parse_can_manage_levels(row["can_manage_levels"]),
                    is_active=bool(row["is_active"]),
                ))

            return levels

        finally:
            conn.close()

    def get_level(self, level: int, tenant_id: str = "default") -> Optional[AdminLevel]:
        """Obtém um nível específico."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT * FROM admin_levels
                WHERE tenant_id = ? AND level = ?
            """, (tenant_id, level))
            row = cursor.fetchone()

            if not row:
                return None

            permissions = []
            if row["permissions"]:
                try:
                    permissions = json.loads(row["permissions"])
                except json.JSONDecodeError:
                    permissions = []

            return AdminLevel(
                id=row["id"],
                tenant_id=row["tenant_id"],
                level=row["level"],
                name=row["name"],
                description=row["description"],
                permissions=permissions,
                can_manage_levels=self._parse_can_manage_levels(row["can_manage_levels"]),
                is_active=bool(row["is_active"]),
            )

        finally:
            conn.close()

    def add_level(
        self,
        tenant_id: str,
        level: int,
        name: str,
        description: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        can_manage_levels: Optional[List[int]] = None,
    ) -> bool:
        """Adiciona novo nível ao tenant."""
        conn = self._get_connection()
        try:
            conn.execute("""
                INSERT INTO admin_levels
                (tenant_id, level, name, description, permissions, can_manage_levels)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                tenant_id,
                level,
                name,
                description,
                json.dumps(permissions or []),
                self._serialize_can_manage_levels(can_manage_levels or []),
            ))
            conn.commit()

            logger.info(f"Admin level {level} added for tenant {tenant_id}")
            return True

        except sqlite3.IntegrityError:
            logger.warning(f"Admin level {level} already exists for tenant {tenant_id}")
            return False

        finally:
            conn.close()

    def update_level(
        self,
        level: int,
        tenant_id: str = "default",
        name: Optional[str] = None,
        description: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        can_manage_levels: Optional[List[int]] = None,
    ) -> bool:
        """Atualiza configuração de um nível."""
        conn = self._get_connection()
        try:
            updates = []
            params = []

            if name is not None:
                updates.append("name = ?")
                params.append(name)

            if description is not None:
                updates.append("description = ?")
                params.append(description)

            if permissions is not None:
                updates.append("permissions = ?")
                params.append(json.dumps(permissions))

            if can_manage_levels is not None:
                updates.append("can_manage_levels = ?")
                params.append(self._serialize_can_manage_levels(can_manage_levels))

            if not updates:
                return False

            updates.append("updated_at = datetime('now')")
            params.extend([tenant_id, level])

            query = f"""
                UPDATE admin_levels
                SET {', '.join(updates)}
                WHERE tenant_id = ? AND level = ?
            """

            cursor = conn.execute(query, params)
            conn.commit()

            if cursor.rowcount > 0:
                logger.info(f"Admin level {level} updated for tenant {tenant_id}")
                return True
            return False

        finally:
            conn.close()

    def delete_level(self, level: int, tenant_id: str = "default") -> bool:
        """Remove um nível do tenant."""
        # Não permite remover nível 0 (Dono)
        if level == 0:
            logger.warning("Cannot delete admin level 0 (owner)")
            return False

        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                DELETE FROM admin_levels
                WHERE tenant_id = ? AND level = ?
            """, (tenant_id, level))
            conn.commit()

            if cursor.rowcount > 0:
                logger.info(f"Admin level {level} deleted for tenant {tenant_id}")
                return True
            return False

        finally:
            conn.close()

    # =========================================================
    # USER ADMIN LEVEL MANAGEMENT
    # =========================================================

    def get_user_level(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Obtém informações do nível admin do usuário."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT u.user_id, u.username, u.email, u.role, u.admin_level,
                       u.tenant_id,
                       al.name as level_name, al.description as level_description,
                       al.permissions, al.can_manage_levels
                FROM users u
                LEFT JOIN admin_levels al
                    ON u.tenant_id = al.tenant_id AND u.admin_level = al.level
                WHERE u.user_id = ?
            """, (user_id,))
            row = cursor.fetchone()

            if not row:
                return None

            permissions = []
            if row["permissions"]:
                try:
                    permissions = json.loads(row["permissions"])
                except json.JSONDecodeError:
                    permissions = []

            return {
                "userId": row["user_id"],
                "username": row["username"],
                "email": row["email"],
                "role": row["role"],
                "tenantId": row["tenant_id"] or "default",
                "adminLevel": row["admin_level"],
                "levelName": row["level_name"],
                "levelDescription": row["level_description"],
                "permissions": permissions,
                "canManageLevels": self._parse_can_manage_levels(row["can_manage_levels"]),
            }

        finally:
            conn.close()

    def set_user_level(
        self,
        user_id: int,
        admin_level: Optional[int],
        set_by_user_id: int,
    ) -> Dict[str, Any]:
        """
        Define nível admin de um usuário.

        Validações:
        - Quem define deve ter nível menor que o nível sendo atribuído
        - Nível 1 só pode ser atribuído por outro nível 1
        """
        conn = self._get_connection()
        try:
            # Obter nível do usuário que está fazendo a alteração
            cursor = conn.execute("""
                SELECT admin_level, tenant_id FROM users WHERE user_id = ?
            """, (set_by_user_id,))
            setter = cursor.fetchone()

            if not setter:
                return {"success": False, "message": "Usuário autorizador não encontrado"}

            setter_level = setter["admin_level"]
            tenant_id = setter["tenant_id"] or "default"

            # Validar permissão
            if setter_level is None:
                return {"success": False, "message": "Você não tem permissão de admin"}

            # Nível 0 só pode ser atribuído por outro nível 0
            if admin_level == 0 and setter_level != 0:
                return {"success": False, "message": "Apenas o Dono pode promover alguém a Dono"}

            # Não pode atribuir nível menor ou igual ao seu (exceto nível 0)
            if admin_level is not None and admin_level <= setter_level and setter_level != 0:
                return {"success": False, "message": f"Você não pode atribuir nível {admin_level}"}

            # Verificar se o nível existe
            if admin_level is not None:
                level_exists = self.get_level(admin_level, tenant_id)
                if not level_exists:
                    return {"success": False, "message": f"Nível {admin_level} não existe"}

            # Atualizar usuário
            conn.execute("""
                UPDATE users SET admin_level = ? WHERE user_id = ?
            """, (admin_level, user_id))
            conn.commit()

            level_name = "Nenhum"
            if admin_level:
                level_info = self.get_level(admin_level, tenant_id)
                if level_info:
                    level_name = level_info.name

            logger.info(f"User {user_id} admin level set to {admin_level} by user {set_by_user_id}")

            return {
                "success": True,
                "message": f"Nível alterado para: {level_name}",
                "userId": user_id,
                "adminLevel": admin_level,
                "levelName": level_name,
            }

        finally:
            conn.close()

    def get_users_by_level(
        self,
        level: int,
        tenant_id: str = "default",
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Lista usuários com um determinado nível admin."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT u.user_id, u.username, u.email, u.phone_number,
                       u.admin_level, u.registration_date
                FROM users u
                WHERE u.tenant_id = ? AND u.admin_level = ?
                AND u.deleted_at IS NULL
                ORDER BY u.registration_date DESC
                LIMIT ? OFFSET ?
            """, (tenant_id, level, limit, offset))

            users = []
            for row in cursor.fetchall():
                users.append({
                    "userId": row["user_id"],
                    "username": row["username"],
                    "email": row["email"],
                    "phone": row["phone_number"],
                    "adminLevel": row["admin_level"],
                    "registeredAt": row["registration_date"],
                })

            return users

        finally:
            conn.close()

    def get_hierarchy_summary(self, tenant_id: str = "default") -> List[Dict[str, Any]]:
        """Obtém resumo da hierarquia com contagens por nível."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT
                    al.level,
                    al.name,
                    al.description,
                    COUNT(u.user_id) as user_count
                FROM admin_levels al
                LEFT JOIN users u ON u.tenant_id = al.tenant_id
                    AND u.admin_level = al.level
                    AND u.deleted_at IS NULL
                WHERE al.tenant_id = ? AND al.is_active = 1
                GROUP BY al.level
                ORDER BY al.level
            """, (tenant_id,))

            summary = []
            for row in cursor.fetchall():
                summary.append({
                    "level": row["level"],
                    "name": row["name"],
                    "description": row["description"],
                    "userCount": row["user_count"],
                })

            return summary

        finally:
            conn.close()

    def can_user_manage(self, manager_user_id: int, target_level: int) -> bool:
        """Verifica se um usuário pode gerenciar um determinado nível."""
        user_info = self.get_user_level(manager_user_id)
        if not user_info or user_info["adminLevel"] is None:
            return False

        # Nível 0 (Dono) pode gerenciar todos
        if user_info["adminLevel"] == 0:
            return True

        # Verificar se o nível alvo está na lista de níveis gerenciáveis
        return target_level in user_info["canManageLevels"]

    def renumber_level(
        self,
        old_level: int,
        new_level: int,
        tenant_id: str = "default",
    ) -> Dict[str, Any]:
        """
        Renumera um nível, migrando todos os usuários para o novo número.

        Etapas:
        1. Verifica se o novo número já não existe
        2. Cria o novo nível com os mesmos dados
        3. Migra todos os usuários para o novo nível
        4. Atualiza referências em canManageLevels de outros níveis
        5. Remove o nível antigo
        """
        # Não permite renumerar nível 0
        if old_level == 0:
            return {"success": False, "message": "Não é possível renumerar o nível 0 (Dono)"}

        if old_level == new_level:
            return {"success": False, "message": "O novo número é igual ao atual"}

        conn = self._get_connection()
        try:
            # Verificar se o nível antigo existe
            old_level_data = self.get_level(old_level, tenant_id)
            if not old_level_data:
                return {"success": False, "message": f"Nível {old_level} não encontrado"}

            # Verificar se o novo número já existe
            new_level_exists = self.get_level(new_level, tenant_id)
            if new_level_exists:
                return {"success": False, "message": f"Nível {new_level} já existe"}

            # Contar usuários afetados
            users = self.get_users_by_level(old_level, tenant_id, limit=1000)
            user_count = len(users)

            # 1. Criar novo nível com os mesmos dados
            conn.execute("""
                INSERT INTO admin_levels
                (tenant_id, level, name, description, permissions, can_manage_levels)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                tenant_id,
                new_level,
                old_level_data.name,
                old_level_data.description,
                json.dumps(old_level_data.permissions),
                self._serialize_can_manage_levels(old_level_data.can_manage_levels),
            ))

            # 2. Migrar usuários para o novo nível
            conn.execute("""
                UPDATE users
                SET admin_level = ?
                WHERE tenant_id = ? AND admin_level = ?
            """, (new_level, tenant_id, old_level))

            # 3. Atualizar referências em canManageLevels de outros níveis
            # Buscar todos os níveis que referenciam o nível antigo
            cursor = conn.execute("""
                SELECT id, level, can_manage_levels
                FROM admin_levels
                WHERE tenant_id = ? AND can_manage_levels LIKE ?
            """, (tenant_id, f"%{old_level}%"))

            for row in cursor.fetchall():
                current_levels = self._parse_can_manage_levels(row["can_manage_levels"])
                if old_level in current_levels:
                    # Substituir referência antiga pela nova
                    current_levels.remove(old_level)
                    current_levels.append(new_level)
                    conn.execute("""
                        UPDATE admin_levels
                        SET can_manage_levels = ?
                        WHERE id = ?
                    """, (self._serialize_can_manage_levels(current_levels), row["id"]))

            # 4. Remover nível antigo
            conn.execute("""
                DELETE FROM admin_levels
                WHERE tenant_id = ? AND level = ?
            """, (tenant_id, old_level))

            conn.commit()

            logger.info(f"Admin level {old_level} renumbered to {new_level} for tenant {tenant_id}. {user_count} users migrated.")

            return {
                "success": True,
                "message": f"Nível renumerado de {old_level} para {new_level}",
                "oldLevel": old_level,
                "newLevel": new_level,
                "usersMigrated": user_count,
            }

        except Exception as e:
            conn.rollback()
            logger.error(f"Error renumbering level: {e}")
            return {"success": False, "message": f"Erro ao renumerar: {str(e)}"}

        finally:
            conn.close()


# Instância global (singleton)
_admin_level_service: Optional[AdminLevelService] = None


def get_admin_level_service() -> AdminLevelService:
    """Obtém instância global do AdminLevelService."""
    global _admin_level_service
    if _admin_level_service is None:
        _admin_level_service = AdminLevelService()
    return _admin_level_service
