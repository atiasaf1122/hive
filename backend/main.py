"""FastAPI application entry point for HIVE backend."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.cost_http import router as cost_router
from backend.api.detection_http import router as detection_router
from backend.api.http import router as http_router
from backend.api.install_http import router as install_router
from backend.api.lifecycle_http import router as lifecycle_router
from backend.api.pipelines_http import router as pipelines_router
from backend.api.preflight_http import router as preflight_router
from backend.api.safety_http import router as safety_router
from backend.api.security_http import router as security_router
from backend.api.registries_http import router as registries_router
from backend.api.skills_search_http import router as skills_search_router
from backend.api.summarizer_http import router as summarizer_router
from backend.api.usage_http import router as usage_router
from backend.api.validation_http import router as validation_router
from backend.api.ws import router as ws_router
from backend.persistence.db import DB_PATH, init_db
from backend.persistence.recovery import run_startup_recovery
from backend.pipelines.scheduler import start_scheduler, stop_scheduler
from backend.telegram.bot import start_bot, stop_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db(DB_PATH)

    crashed = await run_startup_recovery(DB_PATH)
    if crashed:
        logger.warning("Startup recovery cleaned up %d crashed agent(s)", len(crashed))

    await start_scheduler(DB_PATH)
    await start_bot()
    logger.info("HIVE backend ready — http://localhost:8765")
    yield
    await stop_bot()
    stop_scheduler()


app = FastAPI(
    title="HIVE",
    version="0.6.0",
    description="AI agent swarm orchestration",
    lifespan=lifespan,
)

# CORS — registered before routers so it wraps every handler.
# Origins listed:
#   :5173            old web frontend (Phase 5) — kept for back-compat
#   :1420            Tauri dev server (Vite, Phase 9A+)
#   tauri://localhost
#   https://tauri.localhost   the two schemes the Tauri WebView uses once
#                             the dev bundle is loaded from disk
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "https://tauri.localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(http_router)
app.include_router(ws_router)
app.include_router(pipelines_router)
app.include_router(cost_router)
app.include_router(registries_router)
app.include_router(usage_router)
app.include_router(detection_router)
app.include_router(lifecycle_router)
app.include_router(preflight_router)
app.include_router(security_router)
app.include_router(safety_router)
app.include_router(validation_router)
app.include_router(install_router)
app.include_router(skills_search_router)
app.include_router(summarizer_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.6.0"}
