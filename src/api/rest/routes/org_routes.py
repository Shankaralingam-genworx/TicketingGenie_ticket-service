from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.dependencies import require_role
from src.core.services.ticket_service import TicketService
from src.data.clients.postgres_client import get_db
from src.schemas.ticket_filter_schema import parse_ticket_filters, TicketFilterParams
from src.schemas.ticket_schema import PaginatedTicketResponse


router = APIRouter(prefix="/organisations", tags=["Organisation Tickets"])

@router.get(
    "/me/tickets",
    response_model=PaginatedTicketResponse,
    summary="org_admin: list all tickets raised by customers in own organisation",
)
async def get_org_tickets(
    current_user: dict               = Depends(require_role("org_admin")),
    filters:      TicketFilterParams = Depends(parse_ticket_filters),
    db:           AsyncSession       = Depends(get_db),
) -> PaginatedTicketResponse:
  
    org_id = current_user.get("org_id")

    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organisation associated with this account.",
        )

    return await TicketService(db).get_org_tickets(
        org_id=int(org_id),
        filters=filters,
    )
