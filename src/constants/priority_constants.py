"""Priority-related enums and mappings."""

from enum import Enum

from src.constants.sla_constants import Severity


class Priority(str, Enum):
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"
    P4 = "p4"


# Maps severity -> priority
SEVERITY_TO_PRIORITY: dict[Severity, Priority] = {
    Severity.CRITICAL: Priority.P1,
    Severity.HIGH: Priority.P2,
    Severity.MEDIUM: Priority.P3,
    Severity.LOW: Priority.P4,
}
