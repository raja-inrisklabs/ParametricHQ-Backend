from fastapi import Request, status
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger("parametric_platform.exceptions")


class PlatformException(Exception):
    def __init__(self, message: str, status_code: int = status.HTTP_400_BAD_REQUEST, details: dict = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class ProviderNotFoundError(PlatformException):
    def __init__(self, provider_id: str):
        super().__init__(
            message=f"Provider '{provider_id}' is not supported or registered.",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"provider_id": provider_id}
        )


class DatasetNotFoundError(PlatformException):
    def __init__(self, data_key: str):
        super().__init__(
            message=f"Dataset with key '{data_key}' was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"data_key": data_key}
        )


class ProviderError(PlatformException):
    def __init__(self, provider_id: str, reason: str):
        super().__init__(
            message=f"Provider '{provider_id}' request failed: {reason}",
            status_code=status.HTTP_502_BAD_GATEWAY,
            details={"provider_id": provider_id, "reason": reason}
        )


class InvalidParameterError(PlatformException):
    def __init__(self, message: str, details: dict = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=details
        )


async def platform_exception_handler(request: Request, exc: PlatformException):
    logger.warning(f"Handled error on {request.url.path}: {exc.message} (details={exc.details})")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "detail": exc.message,
            "details": exc.details
        }
    )
