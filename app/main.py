from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text as sa_text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db

from app.api.accounts import router as accounts_router
from app.api.football import router as football_router
from app.api.live_ws import router as live_router
from app.api.scouting import router as scouting_router
from app.api.scouting_discovery import router as discovery_router

app = FastAPI(title="FootScore API", version="0.20.0")
app.include_router(football_router)
app.include_router(scouting_router)
app.include_router(discovery_router)
app.include_router(accounts_router)
app.include_router(live_router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["system"])
def readiness(db: Session = Depends(get_db)) -> dict[str, str]:
    """Check database connectivity; does not certify migrations or upstream APIs."""
    try:
        db.execute(sa_text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ready", "database": "connected"}


@app.get("/api/v1/health", tags=["system"])
def api_health() -> dict[str, str]:
    return {"status": "ok"}
