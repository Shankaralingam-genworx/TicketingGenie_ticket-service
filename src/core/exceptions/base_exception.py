"""Base application exception."""


class AppException(Exception):
    """All domain/service layer errors raise this. Caught by error_handler middleware."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundException(AppException):
    def __init__(self, resource: str, id: int | str):
        super().__init__(f"{resource} with id={id} not found.", status_code=404)


class ForbiddenException(AppException):
    def __init__(self, detail: str = "You do not have permission to perform this action."):
        super().__init__(detail, status_code=403)


class ConflictException(AppException):
    def __init__(self, detail: str):
        super().__init__(detail, status_code=409)


class InvalidTransitionException(AppException):
    def __init__(self, current: str, target: str):
        super().__init__(
            f"Invalid status transition: '{current}' → '{target}'.", status_code=422
        )
