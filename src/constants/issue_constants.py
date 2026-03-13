"""Issue-related enums."""

from enum import Enum


class IssueCategory(str, Enum):
    LOGIN_PROBLEM = "login_problem"
    ACCESS_ISSUE = "access_issue"
    BUG_OR_ERROR = "bug_or_error"
    PERFORMANCE_ISSUE = "performance_issue"
    FEATURE_REQUEST = "feature_request"
    BILLING_ISSUE = "billing_issue"
    ACCOUNT_MANAGEMENT = "account_management"
    DATA_ISSUE = "data_issue"
    INTEGRATION_ISSUE = "integration_issue"
    SECURITY_CONCERN = "security_concern"
    OTHER = "other"