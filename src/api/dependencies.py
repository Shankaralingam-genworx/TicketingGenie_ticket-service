"""FastAPI request-level dependencies."""

from fastapi import Depends, HTTPException, Request


def get_current_user(request: Request) -> dict:
    """
    Extract user context from request.state (injected by AuthMiddleware).
    Returns dict with: user_id (int), role (str), tier (str|None), team_id (int|None).
    """
    user_id = getattr(request.state, "user_id", None)
    email  = getattr(request.state, "email", None)
    role = getattr(request.state, "role", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "user_id": user_id,
        "email":email,
        "role": role,
        "customer_tier": getattr(request.state, "customer_tier", None),
        "team_id": getattr(request.state, "team_id", None),
    }


def require_role(*roles: str):
    """Dependency factory for role-based access control."""

    def checker(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user["role"] not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required roles: {', '.join(roles)}",
            )
        return current_user

    return checker
