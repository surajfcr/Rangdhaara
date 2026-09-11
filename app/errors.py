"""One error shape for every API response: {"error": {"code", "message", "fields"?}}."""
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: dict | None = None, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields
        self.extra = extra or {}


def bad_request(message: str, code: str = "invalid_request", fields: dict | None = None, **extra) -> ApiError:
    return ApiError(400, code, message, fields, extra)


def not_found(message: str = "Not found.") -> ApiError:
    return ApiError(404, "not_found", message)


def unauthorized(message: str = "Please sign in to continue.", code: str = "unauthorized") -> ApiError:
    return ApiError(401, code, message)


def forbidden(message: str = "You don't have access to this.") -> ApiError:
    return ApiError(403, "forbidden", message)


def conflict(message: str, code: str = "conflict", **extra) -> ApiError:
    return ApiError(409, code, message, None, extra)


def too_many(message: str = "Too many attempts. Please wait a few minutes and try again.") -> ApiError:
    return ApiError(429, "rate_limited", message)


def _body(code: str, message: str, fields=None, extra=None) -> dict:
    error = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    if extra:
        error.update(extra)
    return {"error": error}


def install(app) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(_body(exc.code, exc.message, exc.fields, exc.extra), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = {}
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path")]
            fields[".".join(loc) or "request"] = err.get("msg", "Invalid value")
        return JSONResponse(_body("invalid_request", "Some details need fixing.", fields), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
            return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)
        from .pages import error_page  # late import: pages imports config only

        return error_page(exc.status_code)
