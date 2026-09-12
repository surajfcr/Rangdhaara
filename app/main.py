"""Application entry point: `uvicorn app.main:app` (or `python manage.py serve`)."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import errors, jobs, pages, seed
from .config import PUBLIC_DIR, settings
from .db import connect, migrate
from .routers import academy_api, account_api, admin_api, auth_api, store_api


@asynccontextmanager
async def lifespan(_: FastAPI):
    problems = settings.problems()
    if problems:
        raise RuntimeError("Refusing to start in production until these are fixed:\n  - " + "\n  - ".join(problems))
    settings.uploads_dir.joinpath("products").mkdir(parents=True, exist_ok=True)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    migrate()
    conn = connect()
    try:
        seed.ensure_seeded(conn)
    finally:
        conn.close()
    runner = jobs.BackgroundJobs()
    runner.start()
    try:
        yield
    finally:
        runner.stop()


app = FastAPI(
    title="Rangdhara",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/api/openapi.json",
)
errors.install(app)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def security_layer(request: Request, call_next):
    path = request.url.path
    # CSRF: browsers can't attach a custom header to a cross-site form post, and our API sends no CORS
    # headers, so a cross-site fetch carrying it is refused at preflight. Gateways' webhooks are exempt.
    if path.startswith("/api/") and request.method in UNSAFE_METHODS and not path.startswith("/api/v1/webhooks/"):
        if request.headers.get("x-requested-with") != "rangdhaara":
            return JSONResponse({"error": {"code": "csrf", "message": "Request blocked. Refresh the page and try again."}},
                                status_code=403)
    response = await call_next(request)
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'; object-src 'none'")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if path.startswith("/api/"):
        headers.setdefault("Cache-Control", "no-store")
    elif path.startswith("/assets/") and path.endswith((".js", ".css")):
        # Revalidate scripts and styles on every load (a cheap ETag check) so a deploy is never half-cached.
        headers.setdefault("Cache-Control", "no-cache")
    if settings.cookie_secure:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(auth_api.router, prefix="/api/v1/auth")
app.include_router(store_api.router, prefix="/api/v1")
app.include_router(account_api.router, prefix="/api/v1/me")
app.include_router(academy_api.router, prefix="/api/v1")
app.include_router(admin_api.router, prefix="/api/v1/admin")
app.include_router(pages.router)

# Only these folders are publicly served. The database, private media, secrets and source code are not reachable.
# The uploads mount is registered first: Starlette matches mounts in order, so it takes priority over
# the general /assets mount below for anything under /assets/uploads — letting UPLOADS_DIR live on a
# separate persistent volume (its own disk on a host, rather than inside the app's own checkout) while
# everything else (css, js, seed images) still ships with the code. It's gitignored (uploads are user
# content, not source), so a fresh checkout must create it before StaticFiles will mount it.
settings.uploads_dir.joinpath("products").mkdir(parents=True, exist_ok=True)
app.mount("/assets/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")
app.mount("/assets", StaticFiles(directory=PUBLIC_DIR / "assets"), name="assets")
