"""Embed the latest published dataset into the site template.

Outputs:
  site/dist/index.html        — page body only (what the Artifact publisher wraps)
  site/dist/standalone.html   — full HTML document (GitHub Pages / any static host)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dailypicks.pipeline import LATEST_PATH  # noqa: E402

TEMPLATE = ROOT / "site" / "index.template.html"
DIST = ROOT / "site" / "dist"


def build(latest: Path = LATEST_PATH) -> tuple[Path, Path]:
    doc = json.loads(latest.read_text())
    slim = _slim(doc)
    payload = json.dumps(slim, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text().replace("/*__DATA__*/", payload)
    DIST.mkdir(parents=True, exist_ok=True)
    body = DIST / "index.html"
    body.write_text(html)
    standalone = DIST / "standalone.html"
    standalone.write_text('<!doctype html><html lang="en"><head><meta charset="utf-8">' + html.split("<style>", 1)[0].replace("<title>", "<title>", 1)
                          + "<style>" + html.split("<style>", 1)[1].split("</style>", 1)[0] + "</style></head><body>"
                          + "</style>".join(html.split("</style>", 1)[1:]) + "</body></html>")
    return body, standalone


def _slim(doc: dict) -> dict:
    """Drop bulky per-fixture fields the page does not render."""
    out = json.loads(json.dumps(doc))
    for day in out["days"]:
        for f in day["fixtures"]:
            f.pop("home_raw", None); f.pop("away_raw", None); f.pop("distribution", None)
            if f.get("sim"):
                d = f["sim"].get("diagnostics", {})
                f["sim"]["diagnostics"] = {k: d[k] for k in ("sim_mean_total", "sim_sd_total", "sim_p_0_0") if k in d}
    return out


if __name__ == "__main__":
    b, s = build()
    print(b, b.stat().st_size, s, s.stat().st_size)
