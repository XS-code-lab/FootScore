from fastapi import FastAPI

app = FastAPI(title="FootScore API", version="0.1.0")


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health", tags=["system"])
def api_health() -> dict[str, str]:
    return {"status": "ok"}
