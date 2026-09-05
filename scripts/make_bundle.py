"""Pack the repository + persistent state into a single HTML "ops bundle" page.

The bundle is the state store for the hosted daily refresh when no git remote is
available: the scheduled cloud session reads the page, extracts the tarball,
runs the pipeline, and republishes the bundle with the updated ledger.

Usage: python scripts/make_bundle.py OUT.html
Restore: python scripts/make_bundle.py --restore BUNDLE.html DEST_DIR
"""
from __future__ import annotations

import base64
import html
import io
import re
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["dailypicks", "scripts", "site/index.template.html", "tests", "docs", "README.md", "OPERATIONS.md", "requirements.txt",
           ".gitignore", ".github", "data/ledger.sqlite", "data/promoted_priors.json", "data/watchlist", "data/fdcouk",
           "data/eval/test_frozen.json", "data/published/latest.json", "data/published/status.json"]


def pack(out: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in INCLUDE:
            p = ROOT / rel
            if p.exists():
                tar.add(p, arcname=rel, filter=lambda ti: None if "__pycache__" in ti.name or ti.name.endswith(".pyc") else ti)
    b64 = base64.b64encode(buf.getvalue()).decode()
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    stamp = datetime.now(timezone.utc).isoformat()
    page = f"""<title>DailyPicks Ops Bundle</title>
<style>body{{font:14px/1.5 system-ui,sans-serif;max-width:70ch;margin:24px auto;padding:0 16px;background:#F5F7F3;color:#1A1F1B}}pre{{display:none}}code{{background:#E6E9E3;padding:1px 4px}}</style>
<h1>DailyPicks Ops Bundle</h1>
<p>Machine-readable state for the DailyPicks daily refresh. Packed {stamp} from commit <code>{commit}</code>
({len(b64) // 1024} KB base64 tar.gz). Contains the pipeline source, the immutable prediction ledger, priors, closing-odds extracts,
the frozen evaluation and the last published board. Restore with
<code>python scripts/make_bundle.py --restore bundle.html dir</code>.</p>
<pre id="dailypicks-bundle" data-packed="{stamp}" data-commit="{commit}">{b64}</pre>
"""
    out.write_text(page)


def restore(bundle: Path, dest: Path) -> None:
    text = bundle.read_text()
    m = re.search(r'<pre id="dailypicks-bundle"[^>]*>([A-Za-z0-9+/=\s]+)</pre>', text)
    if not m:
        raise SystemExit("bundle payload not found")
    raw = base64.b64decode(html.unescape(m.group(1)).strip())
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        tar.extractall(dest)
    print("restored to", dest)


if __name__ == "__main__":
    if sys.argv[1] == "--restore":
        restore(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        pack(Path(sys.argv[1]))
        print("bundle written", sys.argv[1])
