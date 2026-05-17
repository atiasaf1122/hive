"""FastAPI application entry point for HIVE backend."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.http import router as http_router
from backend.api.pipelines_http import router as pipelines_router
from backend.api.ws import router as ws_router
from backend.persistence.db import DB_PATH, init_db
from backend.pipelines.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db(DB_PATH)
    await start_scheduler(DB_PATH)
    logger.info("HIVE backend ready — http://localhost:8765")
    yield
    stop_scheduler()


app = FastAPI(
    title="HIVE",
    version="0.6.0",
    description="AI agent swarm orchestration",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(http_router)
app.include_router(ws_router)
app.include_router(pipelines_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.6.0"}
