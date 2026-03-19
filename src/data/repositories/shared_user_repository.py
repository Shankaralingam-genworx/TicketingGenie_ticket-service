"""
Shared user repository — read-only queries against auth service tables.

File path: src/data/repositories/shared_user_repository.py

Because both services share the same PostgreSQL database, the ticket service
can query `users` and `team_members` directly without any HTTP call to the
auth service.

Rules:
  - READ ONLY — this repository never inserts, updates, or deletes.
  - No imports from the auth service codebase.
  - Uses raw SQL via SQLAlchemy text() so the ticket service has no dependency
    on auth service ORM models.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SharedUserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_user_email(self, user_id: int) -> str | None:
        """Return the email of a user by their id, or None if not found."""
        result = await self.db.execute(
            text("SELECT email FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
        row = result.fetchone()
        return row.email if row else None

    async def get_user_info(self, user_id: int) -> dict | None:
        """Return id, name and email of a user, or None if not found."""
        result = await self.db.execute(
            text("SELECT id, name, email FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
        row = result.fetchone()
        return {"id": row.id, "name": row.name, "email": row.email} if row else None

    async def get_team_lead(self, team_id: int) -> dict | None:
        """
        Return id, name and email of the team lead for the given team,
        or None if the team has no lead or the team does not exist.

        Reads directly from the shared DB (teams + users tables in auth schema)
        so no HTTP call to the auth service is needed.
        """
        result = await self.db.execute(
            text("""
                SELECT u.id, u.name, u.email
                FROM teams t
                JOIN users u ON u.id = t.team_lead_id
                WHERE t.id = :team_id
                  AND u.is_active = TRUE
            """),
            {"team_id": team_id},
        )
        row = result.fetchone()
        return {"id": row.id, "name": row.name, "email": row.email} if row else None

    async def get_user(self, user_id: int) -> dict | None:
        """Return id, name, email of a user, or None if not found."""
        result = await self.db.execute(
            text("SELECT id, name, email FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
        row = result.fetchone()
        return {"id": row.id, "name": row.name, "email": row.email} if row else None

    async def get_team_lead(self, team_id: int) -> dict | None:
        """
        Return id, name, email of the team lead for a given team.
        Joins teams.team_lead_id → users.  Returns None if team or lead not found.
        """
        result = await self.db.execute(
            text("""
                SELECT u.id, u.name, u.email
                FROM teams t
                JOIN users u ON u.id = t.team_lead_id
                WHERE t.id = :team_id
            """),
            {"team_id": team_id},
        )
        row = result.fetchone()
        return {"id": row.id, "name": row.name, "email": row.email} if row else None

    async def get_team_lead_by_team_id(self, team_id: int) -> dict | None:
        """
        Return the team lead user info for a given team_id, or None.
        Shape: {"id": int, "name": str, "email": str}

        Queries the shared `teams` + `users` tables directly — no HTTP call needed.
        """
        result = await self.db.execute(
            text("""
                SELECT u.id, u.name, u.email
                FROM users u
                JOIN teams t ON t.team_lead_id = u.id
                WHERE t.id = :team_id
                  AND u.is_active = TRUE
            """),
            {"team_id": team_id},
        )
        row = result.fetchone()
        return {"id": row.id, "name": row.name, "email": row.email} if row else None

    async def get_team_agents(self, team_id: int) -> list[dict]:
        """
        Return only active SUPPORT AGENT members of a team as plain dicts.
        Shape: [{"id": int, "name": str, "email": str}, ...]

        Joins roles to explicitly exclude the team_lead — the team lead is
        also stored in team_members (added by admin_service.create_team /
        update_team), so without this filter they appear in the assign
        dropdown and agent workload list.
        """
        result = await self.db.execute(
            text("""
                SELECT u.id, u.name, u.email
                FROM users u
                JOIN team_members tm ON tm.user_id = u.id
                JOIN roles r         ON r.id        = u.role_id
                WHERE tm.team_id   = :team_id
                  AND u.is_active  = TRUE
                  AND r.name       = 'support_agent'
                ORDER BY u.name
            """),
            {"team_id": team_id},
        )
        return [
            {"id": row.id, "name": row.name, "email": row.email}
            for row in result.fetchall()
        ]