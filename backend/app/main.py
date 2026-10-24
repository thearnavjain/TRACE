from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.compare import router as compare_router
from app.api.discovery import router as discovery_router

app = FastAPI(title="TRACE API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(compare_router)
app.include_router(discovery_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "trace-api"}
