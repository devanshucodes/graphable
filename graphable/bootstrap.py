"""One command from nothing to a demo-ready database:
generate synthetic data -> load systems of record -> extract knowledge from prose -> golden snapshots.

  python -m graphable.bootstrap            # both tenants
"""
import argparse
import time

from .ingest.loader import load, make_golden
from .ingest.pipeline import run as extract
from .settings import settings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="all")
    ap.add_argument("--regenerate", action="store_true", help="re-run the synthetic data generator first")
    ap.add_argument("--no-embed", action="store_true", help="skip Gemini embeddings even if a key is set")
    a = ap.parse_args()
    t = time.time()
    if a.regenerate or not (settings.data_dir / "acme" / "manifest.json").exists():
        from datagen.__main__ import generate
        from datagen.config import Config, globex_config
        generate(Config(), settings.data_dir, "gemini" if settings.llm_enabled else "template")
        generate(globex_config(), settings.data_dir, "gemini" if settings.llm_enabled else "template")
    for tenant in (["acme", "globex"] if a.tenant == "all" else [a.tenant]):
        load(tenant)
        extract(tenant)
        if settings.llm_enabled and not a.no_embed:
            from .core.embed import embed_tenant
            embed_tenant(tenant)
        make_golden(tenant)
    print(f"bootstrap done in {time.time() - t:.1f}s")


if __name__ == "__main__":
    main()
