import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from scout_api.auth import require_owner
from scout_api.deps import api_limit
from scout_api.routes import jobs, meta, resume, telegram
from scout_core.config import get_settings

structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"),
                                structlog.processors.add_log_level,
                                structlog.processors.JSONRenderer()])

settings = get_settings()
if settings.is_production and settings.dev_auth_token:
    raise RuntimeError("DEV_AUTH_TOKEN must not be set in production")
app = FastAPI(title="Scout API", version="0.1.0",
              docs_url=None if settings.is_production else "/docs", redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

owner_only = [Depends(require_owner), Depends(api_limit)]
app.include_router(jobs.router, dependencies=owner_only)
app.include_router(jobs.labels_router, dependencies=owner_only)
app.include_router(meta.router, dependencies=owner_only)
app.include_router(resume.router, dependencies=owner_only)
app.include_router(telegram.router)  # authenticated by Telegram's secret header instead


@app.get("/healthz")
async def healthz():
    return {"ok": True}
