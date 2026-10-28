import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rca_sal.pipeline import load_config  # noqa: E402

CFG = load_config(ROOT / "config" / "cruises.toml")
Y25 = CFG["years"]["2025"]


_SOURCE = None


def resolve_spec(spec):
    """Local path of a config input. A `github:` input is fetched through the cache; if GitHub is unreachable the returned path does not exist and dependent tests are skipped."""
    global _SOURCE
    from rca_sal import github_data
    if github_data.is_github_spec(spec):
        cache = ROOT / "data" / "cache" / "github"
        rel = github_data.split_spec(spec)[1]
        hit = sorted(cache.glob(f"*/{rel}"), key=lambda f: f.stat().st_mtime) if cache.is_dir() else []
        if hit:                                 # already fetched and hash-verified by an earlier run
            return hit[-1]
        try:
            _SOURCE = _SOURCE or github_data.source_from_config(CFG, ROOT)
            return Path(_SOURCE.fetch(spec)[0])
        except Exception:                       # noqa: BLE001 - offline / not in the repo
            return ROOT / "data" / "cache" / "unavailable" / spec.replace(":", "_")
    p = Path(spec)
    return p if p.is_absolute() else ROOT / p


def recorded_file(content_url: str) -> Path | None:
    """Local file behind a record's contentUrl: its repository path under ROOT; None for a GitHub URL (not a local file)."""
    return None if content_url.startswith("https://") else ROOT / content_url


def path25(role):
    return resolve_spec(Y25["inputs"][role])


def need(*roles):
    """Skip the test when a required input file is not available locally."""
    gone = [str(path25(r)) for r in roles if not path25(r).is_file()]
    return pytest.mark.skipif(bool(gone), reason=f"input missing: {gone}")


@pytest.fixture(scope="session")
def prof25():
    from rca_sal import profiler
    return profiler.read_ooinet_nc(path25("profiler_nc"))


@pytest.fixture(scope="session")
def ctd25():
    from rca_sal import seabird
    return seabird.read_cnv(path25("reference_cnv"))
