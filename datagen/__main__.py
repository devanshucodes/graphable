"""CLI: python -m datagen [--tenant acme|globex|all] [--render template|gemini] [--out datagen/out]"""
import argparse
import json
import sys
import time
from pathlib import Path

from .config import Config, globex_config
from .export import export
from .render import render_all
from .scenarios import ScenarioBuilder
from .timeline import run_timeline
from .validate import validate
from .world import build_world


def generate(cfg: Config, out: Path, render: str) -> dict:
    t = time.time()
    w = build_world(cfg)
    tl, sim = run_timeline(w)
    sb = ScenarioBuilder(w, tl, sim)
    scen = {"test": sb.test_scenarios(), "learning": sb.learning_set(), "hero": sb.hero(), "probes": sb.probes()}
    scen["train"] = sb.train_scenarios()
    render_all(w, tl, render, cache_dir=out.parent / ".cache")
    report = validate(w, tl, scen)
    if not report["ok"]:
        print(json.dumps(report, indent=1)[:4000], file=sys.stderr)
        raise SystemExit(f"validation failed for {cfg.tenant}")
    m = export(w, tl, scen, report, out / cfg.tenant, render, sim)
    print(f"[{cfg.tenant}] {m['estimated_graph_nodes_before_extraction']:,} nodes (est.) in {time.time() - t:.1f}s; "
          f"warnings: {report['warnings'] or 'none'}")
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="all", choices=["acme", "globex", "all"])
    ap.add_argument("--render", default="template", choices=["template", "gemini"])
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    a = ap.parse_args()
    out = Path(a.out)
    if a.tenant in ("acme", "all"):
        generate(Config(), out, a.render)
    if a.tenant in ("globex", "all"):
        generate(globex_config(), out, a.render)


if __name__ == "__main__":
    main()
