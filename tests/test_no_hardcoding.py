"""The package must contain no results, checksums or machine paths: those come from files at run time."""
import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "rca_sal"
CHECKS = {
    "sha256-looking hex string": re.compile(r"\b[0-9a-fA-F]{40,64}\b"),
    "windows/drive path": re.compile(r"\b[A-Za-z]:[\\/]"),
    "user home path": re.compile(r"(/Users/|/home/|Shared drives)"),
    "result-looking float (3+ decimals)": re.compile(r"(?<![\w.])-?\d+\.\d{3,}(?![\w])"),
    "result-looking tiny float": re.compile(r"(?<![\w.])-?0\.0\d{2,}"),
    "fallback/demo marker": re.compile(r"(?i)\b(fallback|demo[_ ]data|dummy|fake|mock)\b"),
}
ALLOWED_FILES = {"pss78.py", "eos80.py"}   # UNESCO PSS-78 polynomial coefficients are physical constants (test_pss78.py)


def test_no_hardcoded_numbers_paths_or_checksums_in_package():
    bad = []
    for f in sorted(PKG.glob("*.py")):
        if f.name in ALLOWED_FILES:
            continue
        for n, line in enumerate(f.read_text(encoding="utf8").splitlines(), 1):
            for label, rx in CHECKS.items():
                m = rx.search(line)
                if m:
                    bad.append(f"{f.name}:{n}: {label}: {m.group(0)!r}: {line.strip()[:90]}")
    assert not bad, "\n".join(bad)


def test_app_has_no_sample_numbers_paths_or_checksums():
    root = PKG.parent
    files = [root / "app.py", root / "app_lib.py", *sorted((root / "app_pages").glob("*.py"))]
    assert all(f.is_file() for f in files)
    for f in files:
        text = f.read_text(encoding="utf8")
        for label in ("sha256-looking hex string", "windows/drive path", "user home path", "fallback/demo marker"):
            assert not CHECKS[label].search(text), f"{f.name}: {label}"
