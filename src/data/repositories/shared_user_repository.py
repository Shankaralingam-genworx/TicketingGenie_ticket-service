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

    async def get_team_agents(self, team_id: int) -> list[dict]:
        """
        Return all active members of a team as plain dicts.
        Shape: [{"id": int, "name": str, "email": str}, ...]

        Equivalent of the auth service endpoint GET /teams/my-agents.
        Queries team_members JOIN users for the given team_id.
        """
        result = await self.db.execute(
            text("""
                SELECT u.id, u.name, u.email
                FROM users u
                JOIN team_members tm ON tm.user_id = u.id
                WHERE tm.team_id = :team_id
                  AND u.is_active = TRUE
                ORDER BY u.name
            """),
            {"team_id": team_id},
        )
        return [
            {"id": row.id, "name": row.name, "email": row.email}
            for row in result.fetchall()
        ]