from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from review_catalog.api.routes import router
from review_catalog.db.session import SessionLocal, create_schema
from review_catalog.services.release import finalize_pending_releases
from review_catalog.services.versions import bootstrap_component_versions
from review_catalog.settings import get_settings


async def _release_finalizer_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    while not stop.is_set():
        await asyncio.to_thread(finalize_pending_releases)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.release_finalizer_poll_seconds)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    create_schema()
    with SessionLocal.begin() as session:
        bootstrap_component_versions(session, settings)
    stop = asyncio.Event()
    task = asyncio.create_task(_release_finalizer_loop(stop))
    yield
    stop.set()
    await task


app = FastAPI(
    title="Review Catalog Platform API",
    version="0.3.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


def run() -> None:
    uvicorn.run("review_catalog.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
