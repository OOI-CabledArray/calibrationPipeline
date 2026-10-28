"""Inputs stored in GitHub repositories, resolved at run time.

"github:<path>" is read from the repository in [github] of the config, "github[name]:<path>" from [github_sources.name]. A ref that
names a branch follows its latest commit; a commit SHA pins the data. Files are cached per commit, verified against the git blob SHA-1
of the repository tree, and returned with commit, blob SHA and URL for the provenance record. The optional GITHUB_TOKEN raises the
API rate limit and is sent to api.github.com only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PREFIX = "github:"
_SPEC = re.compile(r"^github(?:\[(\w+)\])?:(.*)$", re.S)
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"


class GitHubMissing(FileNotFoundError):
    """Some `github:` inputs are not in the repository at the resolved commit (or the repo cannot be reached)."""

    def __init__(self, missing: list[dict]):
        self.missing = missing
        super().__init__("github input(s) unavailable: " + "; ".join(f"{m['role']}: {m['path']} ({m['why']})" for m in missing))


def is_github_spec(value) -> bool:
    return isinstance(value, str) and _SPEC.match(value) is not None


def split_spec(spec: str) -> tuple[str | None, str]:
    """('github[idx]:a/b.csv') -> ('idx', 'a/b.csv');  ('github:a/b.csv') -> (None, 'a/b.csv')."""
    m = _SPEC.match(spec)
    if not m:
        raise ValueError(f"not a github input: {spec!r}")
    return m.group(1), m.group(2).lstrip("/")


def git_blob_sha1(data: bytes) -> str:
    """The SHA-1 git itself assigns to a file's content (matches `git hash-object`)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _http_get(url: str, api: bool = False, timeout: float = 120) -> bytes:
    headers = {"User-Agent": "rca-salinity"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout, context=_tls()) as r:
        return r.read()


def _tls() -> ssl.SSLContext:
    """TLS context with certificate verification, using the certifi CA bundle."""
    import certifi
    return ssl.create_default_context(cafile=certifi.where())


@dataclass
class FileInfo:
    spec: str
    repo: str
    ref: str
    commit: str
    path: str
    blob_sha: str
    size: int
    url: str

    def note(self) -> str:
        return f"{self.spec} @ {self.repo} commit {self.commit} (git blob {self.blob_sha})"


class Source:
    def __init__(self, repo: str, ref: str, cache_dir: str | Path, http_get=_http_get):
        self.repo, self.ref, self.cache_dir, self._get = repo, ref, Path(cache_dir), http_get
        self._commit: str | None = None
        self._tree: dict[str, dict] | None = None

    def commit(self) -> str:
        if self._commit is None:
            if len(self.ref) == 40 and all(c in "0123456789abcdef" for c in self.ref.lower()):
                self._commit = self.ref.lower()
            else:
                self._commit = self._ref_via_git() or self._ref_via_api()
        return self._commit

    def _ref_via_git(self) -> str | None:
        """Resolve a branch/tag with git's own smart-HTTP ref advertisement, which is not subject to the API rate limit."""
        try:
            adv = self._get(f"https://github.com/{self.repo}.git/info/refs?service=git-upload-pack").decode("latin1")
        except Exception:                                    # noqa: BLE001 - fall back to the API
            return None
        for kind in ("heads", "tags"):
            name = re.escape(self.ref)
            peeled = re.search(rf"([0-9a-f]{{40}}) refs/{kind}/{name}\^\{{\}}\n", adv)   # annotated tag -> its commit
            plain = re.search(rf"([0-9a-f]{{40}}) refs/{kind}/{name}\n", adv)
            if peeled or plain:
                return (peeled or plain).group(1)
        return None

    def _ref_via_api(self) -> str:
        info = json.loads(self._get(f"{API}/repos/{self.repo}/commits/{urllib.parse.quote(self.ref, safe='')}", api=True))
        return info["sha"]

    def tree(self) -> dict[str, dict]:
        """path -> {sha, size} of every file at the commit (cached next to the files)."""
        if self._tree is None:
            sha = self.commit()
            cached = self.cache_dir / sha / "_tree.json"
            if cached.is_file():
                entries = json.loads(cached.read_text(encoding="utf8"))
            else:
                data = json.loads(self._get(f"{API}/repos/{self.repo}/git/trees/{sha}?recursive=1", api=True))
                if data.get("truncated"):
                    raise GitHubMissing([{"role": "tree", "path": self.repo, "why": "repository tree too large for one API call"}])
                entries = [{"path": t["path"], "sha": t["sha"], "size": t.get("size", 0)} for t in data["tree"] if t["type"] == "blob"]
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(json.dumps(entries), encoding="utf8")
            self._tree = {e["path"]: e for e in entries}
        return self._tree

    def fetch(self, spec: str) -> tuple[Path, FileInfo]:
        path = split_spec(spec)[1]
        entry = self.tree().get(path)
        if entry is None:
            raise FileNotFoundError(f"{path} is not in {self.repo} at {self.commit()[:10]}")
        sha = self.commit()
        local = self.cache_dir / sha / path
        url = f"{RAW}/{self.repo}/{sha}/{urllib.parse.quote(path)}"
        if not (local.is_file() and git_blob_sha1(local.read_bytes()) == entry["sha"]):
            data = self._get(url)
            if git_blob_sha1(data) != entry["sha"]:
                raise OSError(f"{path}: downloaded bytes do not match the repository's git blob hash")
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
        return local, FileInfo(spec, self.repo, self.ref, sha, path, entry["sha"], entry["size"], url)


def _source(table: dict, root: Path, http_get) -> Source:
    cache = Path(table.get("cache_dir", "data/cache/github"))
    return Source(table["repo"], table.get("ref", "main"), cache if cache.is_absolute() else root / cache, http_get)


def source_from_config(cfg: dict, root: Path, http_get=_http_get) -> Source | None:
    g = cfg.get("github")
    return _source(g, root, http_get) if g else None


def sources_from_config(cfg: dict, root: Path, http_get=_http_get) -> dict[str | None, Source]:
    """{None: the [github] source, name: each [github_sources.name] source}; a named table inherits cache_dir from [github]."""
    out: dict[str | None, Source] = {}
    g = cfg.get("github")
    if g:
        out[None] = _source(g, root, http_get)
    for name, t in cfg.get("github_sources", {}).items():
        out[name] = _source({"cache_dir": (g or {}).get("cache_dir", "data/cache/github"), **t}, root, http_get)
    return out


def resolve_inputs(source, inputs: dict) -> tuple[dict, dict[str, FileInfo]]:
    """Replace every `github:` / `github[name]:` input (single value or list) with its local cached path.

    `source` is one Source (the default), a {name: Source} dict from sources_from_config, or None.
    Returns (resolved inputs, {local path -> FileInfo}). Raises GitHubMissing listing every input that is unavailable.
    """
    sources = source if isinstance(source, dict) else {None: source}
    out, infos, missing = {}, {}, []
    for role, val in inputs.items():
        items = val if isinstance(val, list) else [val]
        done = []
        for v in items:
            if not is_github_spec(v):
                done.append(v)
                continue
            name = split_spec(v)[0]
            src = sources.get(name)
            if src is None:
                missing.append({"role": role, "path": v, "why": "no [github] section in the config" if name is None
                                else f"no [github_sources.{name}] section in the config"})
                continue
            try:
                local, info = src.fetch(v)
            except (FileNotFoundError, OSError, urllib.error.URLError, KeyError) as e:
                missing.append({"role": role, "path": v, "why": str(e)})
                continue
            infos[str(local)] = info
            done.append(str(local))
        out[role] = done if isinstance(val, list) else done[0] if done else val
    if missing:
        raise GitHubMissing(missing)
    return out, infos
