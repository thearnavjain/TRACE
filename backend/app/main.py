import os
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.compare import router as compare_router
from app.api.discovery import router as discovery_router

app = FastAPI(title="TRACE API", version="0.1.0")


def _frontend_origins(frontend_origin: str | None) -> list[str]:
    """Return local development origins and one validated deployment origin."""
    origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    if frontend_origin is None or not frontend_origin.strip():
        return origins

    configured = frontend_origin.strip()
    try:
        parsed = urlsplit(configured)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "FRONTEND_ORIGIN must be a single HTTP(S) origin without credentials, path, query, or fragment."
        ) from exc

    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if (
        not hostname
        or scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or "," in configured
        or "*" in configured
        or "\\" in configured
        or any(character.isspace() or ord(character) < 0x20 for character in configured)
        or (scheme == "http" and hostname.lower() not in local_hosts)
    ):
        raise ValueError(
            "FRONTEND_ORIGIN must be one origin only; use HTTPS except for localhost."
        )

    normalized_host = hostname.lower()
    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    normalized_port = f":{port}" if port is not None and not default_port else ""
    origin = f"{scheme}://{normalized_host}{normalized_port}"
    if origin not in origins:
        origins.append(origin)
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins(os.environ.get("FRONTEND_ORIGIN")),
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(compare_router)
app.include_router(discovery_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "trace-api"}
