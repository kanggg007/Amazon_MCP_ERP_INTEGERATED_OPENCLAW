"""
Role-Based Access Control (RBAC)
===================================
Multi-level admin system for the operations platform.

Master Admin:
  - Can read ALL stores
  - Can write to ALL stores (with approval)
  - Manages users and permissions
  - Still requires Level 2 approval for high-risk writes

Sub Admin:
  - Can ONLY read their assigned stores
  - Can ONLY write to their assigned stores (with approval)
  - Cannot see other stores' data

Authorization:
  - READ: Always allowed (for assigned stores)
  - WRITE: Always requires Level 2 approval (even for Master Admin)
"""

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Set

from pydantic import BaseModel

logger = logging.getLogger("amazon.auth.rbac")


class AdminRole(str, Enum):
    MASTER = "master_admin"       # Master-A: read+write+manage all
    WRITE_ADMIN = "write_admin"    # Master-B: read+write all, no manage
    READ_ADMIN = "read_admin"      # Master-C: read-only all, no write/manage
    SUB = "sub_admin"              # Limited: assigned stores only


class Permission(str, Enum):
    READ = "read"
    WRITE = "write"
    MANAGE_USERS = "manage_users"
    MANAGE_STORES = "manage_stores"


# Permission matrix
ROLE_PERMISSIONS = {
    AdminRole.MASTER: {
        Permission.READ: "all",
        Permission.WRITE: "all",
        Permission.MANAGE_USERS: True,
        Permission.MANAGE_STORES: True,
    },
    AdminRole.WRITE_ADMIN: {
        Permission.READ: "all",
        Permission.WRITE: "all",
        Permission.MANAGE_USERS: False,
        Permission.MANAGE_STORES: False,
    },
    AdminRole.READ_ADMIN: {
        Permission.READ: "all",
        Permission.WRITE: "none",
        Permission.MANAGE_USERS: "sub_only",  # Can manage Sub Admins only
        Permission.MANAGE_STORES: False,
    },
    AdminRole.SUB: {
        Permission.READ: "assigned",
        Permission.WRITE: "assigned",
        Permission.MANAGE_USERS: False,
        Permission.MANAGE_STORES: False,
    },
}


class AdminUser(BaseModel):
    """An admin user in the system."""
    user_id: str
    username: str
    role: AdminRole
    assigned_stores: List[str] = []  # For sub admins: which stores they can access
    write_allowed: bool = True        # Sub admin: False = read-only
    is_active: bool = True
    created_at: str = ""
    created_by: str = ""


class RBACManager:
    """
    Role-based access control for the operations platform.

    Stores user definitions and evaluates permissions.
    All write operations require Level 2 approval regardless of role.
    """

    def __init__(self):
        self._users: Dict[str, AdminUser] = {}
        self._load_defaults()

    def _load_defaults(self):
        """Load admin users from admin_users.json or fallback to env."""
        import os, json as j
        from pathlib import Path
        
        users_file = Path(__file__).parent / "admin_users.json"
        if users_file.exists():
            with open(users_file) as f:
                users_data = j.load(f)
            for uid, data in users_data.items():
                if not data.get("is_active", True):
                    continue
                self._users[uid] = AdminUser(
                    user_id=uid,
                    username=data.get("username", uid),
                    role=AdminRole(data.get("role", "sub_admin")),
                    assigned_stores=data.get("assigned_stores", []),
                    write_allowed=data.get("write_allowed", True),
                    is_active=True,
                    created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
                    created_by=data.get("created_by", "system"),
                )
            logger.info("Loaded %d users from admin_users.json", len(self._users))
        else:
            # Fallback: master admin from env
            master_id = os.environ.get("MASTER_ADMIN_ID", "master")
            self._users[master_id] = AdminUser(
                user_id=master_id, username="Master Admin",
                role=AdminRole.MASTER, assigned_stores=[], is_active=True,
                created_at=datetime.now(timezone.utc).isoformat(),
                created_by="system",
            )
            logger.info("Master admin '%s' initialized from env", master_id)

    # ─── User Management (Master Admin Only) ───

    def create_sub_admin(self, user_id: str, username: str,
                         assigned_stores: List[str],
                         created_by: str) -> AdminUser:
        """
        Create a new sub-admin user (Master Admin only).

        Args:
            user_id: Unique user ID
            username: Display name
            assigned_stores: List of store_ids this admin can access
            created_by: Who created this user (must be Master)

        Returns:
            Created AdminUser

        Raises:
            PermissionError: If creator is not Master Admin
            ValueError: If user_id already exists
        """
        if user_id in self._users:
            raise ValueError(f"User '{user_id}' already exists.")

        creator = self._users.get(created_by)
        if not creator or creator.role != AdminRole.MASTER:
            raise PermissionError("Only Master Admin can create sub-admin users.")

        user = AdminUser(
            user_id=user_id,
            username=username,
            role=AdminRole.SUB,
            assigned_stores=assigned_stores,
            is_active=True,
            created_at=datetime.now(timezone.utc).isoformat(),
            created_by=created_by,
        )
        self._users[user_id] = user
        logger.info("Sub-admin '%s' created by '%s' — stores: %s",
                     user_id, created_by, assigned_stores)
        return user

    def deactivate_user(self, user_id: str, actioned_by: str) -> bool:
        """Deactivate a user (Master Admin only)."""
        if user_id == "master":
            raise PermissionError("Cannot deactivate the Master Admin.")

        actor = self._users.get(actioned_by)
        if not actor or actor.role != AdminRole.MASTER:
            raise PermissionError("Only Master Admin can deactivate users.")

        if user_id in self._users:
            self._users[user_id].is_active = False
            logger.info("User '%s' deactivated by '%s'", user_id, actioned_by)
            return True
        return False

    # ─── Permission Checks ───

    def check_read_access(self, user_id: str, store_id: str) -> bool:
        """Check if a user can READ data from a store."""
        user = self._users.get(user_id)
        if not user or not user.is_active:
            logger.warning("READ denied: user '%s' not found or inactive", user_id)
            return False

        # Master, Write Admin, Read Admin → can read all stores
        if user.role in (AdminRole.MASTER, AdminRole.WRITE_ADMIN, AdminRole.READ_ADMIN):
            return True

        if user.role == AdminRole.SUB:
            return store_id in (user.assigned_stores or [])

        return False

    def check_write_access(self, user_id: str, store_id: str) -> bool:
        """Check if a user can WRITE to a store. Read-only subs always denied."""
        user = self._users.get(user_id)
        if not user or not user.is_active:
            return False

        if user.role == AdminRole.READ_ADMIN:
            return False

        if user.role in (AdminRole.MASTER, AdminRole.WRITE_ADMIN):
            return True

        if user.role == AdminRole.SUB:
            if not user.write_allowed:
                return False  # Read-only sub admin
            return store_id in (user.assigned_stores or [])

        return False

    def check_manage_users(self, user_id: str, target_role: AdminRole = None) -> bool:
        """
        Check if a user can manage other users.
        Master-A: manage ALL users
        Master-C: manage SUB_ADMIN users only
        Master-B: no user management
        """
        user = self._users.get(user_id)
        if not user:
            return False
        if user.role == AdminRole.MASTER:
            return True  # Master-A: unrestricted
        if user.role == AdminRole.READ_ADMIN:
            # Master-C: can only manage Sub Admin users
            if target_role is None or target_role == AdminRole.SUB:
                return True
            return False
        return False  # Master-B and others: denied

    def create_admin(self, user_id: str, username: str, role: AdminRole,
                     created_by: str, assigned_stores: List[str] = None) -> AdminUser:
        """Create any admin type (Master Admin only)."""
        if user_id in self._users:
            raise ValueError(f"User '{user_id}' already exists.")
        creator = self._users.get(created_by)
        if not creator or creator.role != AdminRole.MASTER:
            raise PermissionError("Only Master Admin can create users.")
        user = AdminUser(user_id=user_id, username=username, role=role,
                         assigned_stores=assigned_stores or [], is_active=True,
                         created_at=datetime.now(timezone.utc).isoformat(),
                         created_by=created_by)
        self._users[user_id] = user
        logger.info("Admin '%s' (%s) created by '%s'", user_id, role.value, created_by)
        return user

    def get_user_stores(self, user_id: str) -> List[str]:
        """Get all stores a user has access to."""
        from auth import get_store_registry
        registry = get_store_registry()

        user = self._users.get(user_id)
        if not user:
            return []

        if user.role == AdminRole.MASTER:
            return list(registry.active_stores.keys())

        return [s for s in (user.assigned_stores or []) if registry.is_valid_store(s)]

    def get_user(self, user_id: str) -> Optional[AdminUser]:
        """Get user by ID."""
        return self._users.get(user_id)

    def list_users(self) -> List[AdminUser]:
        """List all users."""
        return list(self._users.values())
