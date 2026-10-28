"""List what each cruise in the shared GitHub repository provides for the pipelines.

Reads the repository tree at the commit that [github] ref in config/cruises.toml resolves to; only the tree listing is downloaded.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rca_sal import github_data  # noqa: E402
from rca_sal.pipeline import load_config  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(ROOT / "config" / "cruises.toml"))
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    src = github_data.source_from_config(cfg, ROOT)
    paths = list(src.tree())
    print(f"{src.repo} @ {src.commit()[:10]} (ref {src.ref}), {len(paths)} files\n")
    print(f"{'cruise':34} {'cnv':>4} {'btl':>4}  {'autosal data':12} {'cast log':9} {'sample log':10} {'winkler':8}")
    for c in sorted({p.split("/")[1] for p in paths if p.startswith("CruiseData/Cabled-")}):
        fs = [p for p in paths if p.startswith(f"CruiseData/{c}/")]
        ext = Counter(p.rsplit(".", 1)[-1].lower() for p in fs if "/CTD Data/" in p)
        top = [p for p in fs if p.count("/") == 2]
        has = lambda pat, pool=top: "yes" if any(re.search(pat, p, re.I) for p in pool) else "-"  # noqa: E731
        wink = sum(1 for p in fs if re.search(r"Winkler_calculations", p, re.I))
        print(f"{c:34} {ext['cnv']:>4} {ext['btl']:>4}  {has('Salinity_Sample_Data'):12} {has('DiscreteCastLogs'):9} "
              f"{has('Salinity_Sample_Log'):10} {wink or '-':>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
