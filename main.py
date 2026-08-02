import logging

from app.common.config import get_settings
from app.common.telemetry import init_telemetry, instrument_fastapi_app

# Settings + telemetry BEFORE FastAPI() so Sentry Starlette/FastAPI integrations patch correctly.
settings = get_settings()
logging.basicConfig(level=logging.INFO)
init_telemetry(settings)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.common import file_router
from app.auth import router as auth_router
from app.units.routers import user as units_user
from app.conference.routers import official as conference_official, public as conference_public
from app.admin.routers import units as admin_units, conference as admin_conference, system as admin_system, site as admin_site, users as admin_users
from app.kalamela.routers import public as kalamela_public, official as kalamela_official, admin as kalamela_admin
from app.yuvalokham.routers import auth as ym_auth, user as ym_user, admin as ym_admin
from app.master import router as master_router

app = FastAPI(title=settings.app_name, version="0.1.0")
instrument_fastapi_app(app)

# Compress responses >= 1 KB (covers JSON list payloads)
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins if settings.cors_origins != ["*"] else [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://csi-webapp-fe.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers - all under /api prefix
app.include_router(file_router.router, prefix="/api/files", tags=["files"])
app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
app.include_router(units_user.router, prefix="/api/units", tags=["units"])
app.include_router(master_router.router, prefix="/api/master", tags=["master"])
app.include_router(conference_official.router, prefix="/api/conference/official", tags=["conference-official"])
app.include_router(conference_public.router, prefix="/api/conference/public", tags=["conference-public"])
app.include_router(admin_units.router, prefix="/api/admin/units", tags=["admin-units"])
app.include_router(admin_conference.router, prefix="/api/admin/conference", tags=["admin-conference"])
app.include_router(admin_system.router, prefix="/api/admin/system", tags=["admin-system"])
app.include_router(admin_system.router, prefix="/api/admin", tags=["admin-system-alias"])
app.include_router(admin_system.router, prefix="/api/system", tags=["system-alias"])
app.include_router(admin_site.router, prefix="/api", tags=["site-settings"])
app.include_router(admin_users.router, prefix="/api/admin/users", tags=["admin-users"])
app.include_router(kalamela_public.router, prefix="/api/kalamela", tags=["kalamela-public"])
app.include_router(kalamela_official.router, prefix="/api/kalamela/official", tags=["kalamela-official"])
app.include_router(kalamela_admin.router, prefix="/api/kalamela/admin", tags=["kalamela-admin"])
app.include_router(ym_auth.router, prefix="/api/yuvalokham/auth", tags=["yuvalokham-auth"])
app.include_router(ym_user.router, prefix="/api/yuvalokham/user", tags=["yuvalokham-user"])
app.include_router(ym_admin.router, prefix="/api/yuvalokham/admin", tags=["yuvalokham-admin"])


@app.get("/api/health", tags=["system"])
async def health() -> dict:
    """Lightweight health check (does not block on database)."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=7000, reload=True)
