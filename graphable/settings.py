"""Runtime configuration from environment (ARCHITECTURE §13)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines); real environment variables always win."""
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    falkordb_host: str = os.getenv("FALKORDB_HOST", "localhost")
    falkordb_port: int = int(os.getenv("FALKORDB_PORT", "6379"))
    falkordb_username: str | None = os.getenv("FALKORDB_USERNAME") or None
    falkordb_password: str | None = os.getenv("FALKORDB_PASSWORD") or None
    query_timeout_ms: int = int(os.getenv("QUERY_TIMEOUT_MS", "8000"))
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None
    gemini_model_pro: str = os.getenv("GEMINI_MODEL_PRO", "gemini-pro-latest")
    gemini_model_flash: str = os.getenv("GEMINI_MODEL_FLASH", "gemini-3.8-flash")
    embed_model: str = os.getenv("EMBED_MODEL", "gemini-embedding-2")
    embed_dim: int = int(os.getenv("EMBED_DIM", "768"))
    mcp_url: str = os.getenv("MCP_URL", "http://127.0.0.1:8765/mcp")
    # tenant API keys: "key1:acme,key2:globex" -> {key: tenant}
    tenant_keys: dict = field(default_factory=lambda: dict(
        kv.split(":", 1) for kv in os.getenv("TENANT_KEYS", "dev-acme:acme,dev-globex:globex,dev-demo:demo_main").split(",") if ":" in kv))
    data_dir: Path = Path(os.getenv("DATA_DIR", str(ROOT / "datagen" / "out")))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.gemini_api_key)


settings = Settings()
