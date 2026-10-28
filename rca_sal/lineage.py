"""SHA-256, size and modification time of source files."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class SourceFile:
    role: str            # e.g. "reference_ctd_cnv", "profiler_nc", "autosal_xlsx"
    path: str
    filename: str
    sha256: str
    size_bytes: int
    modified_utc: str
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def source_file(path: str | Path, role: str, note: str = "") -> SourceFile:
    """Fingerprint a file. Raises FileNotFoundError if it does not exist."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"{role}: source file not found: {p}")
    st = p.stat()
    return SourceFile(
        role=role,
        path=str(p.resolve()),
        filename=p.name,
        sha256=sha256_file(p),
        size_bytes=st.st_size,
        modified_utc=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
        note=note,
    )
