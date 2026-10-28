"""Write the deployment summary from the committed run outputs.

  python scripts/deployments.py   ->   outputs/deployments.yaml, outputs/deployments.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rca_sal.deployments import summarize, to_yaml  # noqa: E402
from rca_sal.pipeline import load_config  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config" / "cruises.toml"))
    ap.add_argument("--out", default=str(ROOT / "outputs"))
    a = ap.parse_args(argv)
    s = summarize(load_config(a.config), a.out)
    out = Path(a.out)
    (out / "deployments.yaml").write_text(to_yaml(s) + "\n", encoding="utf8")
    (out / "deployments.json").write_text(json.dumps(s, indent=2), encoding="utf8")
    for g in s["instruments"]:
        done = [d for d in g["deployments"] if d["calibration"] == "calibrated"]
        print(f"{g['refdes']:30s} {g['variable']:8s} calibrated {len(done)}/{len(g['deployments'])}")
        for d in done:
            print(f"    deployment {d['deployment']} ({d['year']}): {d['status']['level']} {d['status']['label']}")
    print(f"wrote {out / 'deployments.yaml'} and {out / 'deployments.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
