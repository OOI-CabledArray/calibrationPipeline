"""github: inputs: resolver behaviour (stubbed HTTP, no network), reader normalisation, and 'missing' handling."""
import json
import subprocess

import pytest

from rca_sal import discrete, github_data as G
from rca_sal.pipeline import MissingInputError, load_config, run_year, year_variants
from tests.conftest import ROOT

SHA = "a" * 40
FILE_A = b"niskin,salinity\n3,33.8\n"
FILE_B = b"\x00\x01binary\xff"
TREE = {"truncated": False, "tree": [
    {"path": "CruiseData/X/a.csv", "type": "blob", "sha": G.git_blob_sha1(FILE_A), "size": len(FILE_A)},
    {"path": "CruiseData/X/sub dir/b.bin", "type": "blob", "sha": G.git_blob_sha1(FILE_B), "size": len(FILE_B)},
    {"path": "CruiseData/X", "type": "tree", "sha": "t" * 40}]}


class FakeHttp:
    def __init__(self, files=None, tree=TREE):
        self.calls, self.files, self.tree = [], files or {"a.csv": FILE_A, "sub%20dir/b.bin": FILE_B}, tree

    def __call__(self, url, api=False, timeout=0):
        self.calls.append(url)
        if "/commits/" in url:
            return json.dumps({"sha": SHA}).encode()
        if "/git/trees/" in url:
            return json.dumps(self.tree).encode()
        return self.files[url.split(f"{SHA}/CruiseData/X/")[1]]


def src(tmp_path, ref="main", http=None):
    return G.Source("o/r", ref, tmp_path / "cache", http or FakeHttp())


def test_blob_sha1_matches_git_hash_object(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(FILE_B)
    out = subprocess.run(["git", "hash-object", str(f)], capture_output=True, text=True, check=True).stdout.strip()
    assert G.git_blob_sha1(FILE_B) == out


def test_resolve_fetches_caches_by_commit_and_reports_provenance(tmp_path):
    http = FakeHttp()
    s = src(tmp_path, http=http)
    inputs = {"a": "github:CruiseData/X/a.csv", "b": ["github:CruiseData/X/sub dir/b.bin"], "local": "some/local.nc"}
    resolved, infos = G.resolve_inputs(s, inputs)
    assert resolved["local"] == "some/local.nc"                              # non-github inputs pass through untouched
    assert (tmp_path / "cache" / SHA / "CruiseData/X/a.csv").read_bytes() == FILE_A
    assert resolved["b"][0].endswith("b.bin")
    info = infos[resolved["a"]]
    assert info.commit == SHA and info.blob_sha == G.git_blob_sha1(FILE_A) and "raw.githubusercontent.com" in info.url
    assert SHA in info.note() and info.blob_sha in info.note()
    n = len(http.calls)
    G.resolve_inputs(src(tmp_path, http=http), inputs)                      # second run: files come from the cache
    assert not any("raw.githubusercontent" in u for u in http.calls[n:])


def test_new_commit_gets_a_new_cache_folder(tmp_path):
    s = src(tmp_path)
    s.fetch("github:CruiseData/X/a.csv")
    assert (tmp_path / "cache" / SHA).is_dir()
    assert not (tmp_path / "cache" / ("b" * 40)).exists()


def test_pinned_commit_does_not_ask_the_api_for_the_ref(tmp_path):
    http = FakeHttp()
    src(tmp_path, ref=SHA, http=http).fetch("github:CruiseData/X/a.csv")
    assert not any("/commits/" in u for u in http.calls)


def test_corrupted_download_is_rejected(tmp_path):
    s = src(tmp_path, http=FakeHttp(files={"a.csv": b"tampered", "sub%20dir/b.bin": FILE_B}))
    with pytest.raises(OSError, match="do not match"):
        s.fetch("github:CruiseData/X/a.csv")
    assert not (tmp_path / "cache" / SHA / "CruiseData/X/a.csv").exists()


def test_unavailable_inputs_are_all_listed_not_just_the_first(tmp_path):
    with pytest.raises(G.GitHubMissing) as e:
        G.resolve_inputs(src(tmp_path), {"one": "github:CruiseData/X/nope.csv", "two": "github:CruiseData/X/also_nope.csv"})
    assert [m["role"] for m in e.value.missing] == ["one", "two"] and "not in" in e.value.missing[0]["why"]


def test_github_input_without_a_github_section_is_missing_not_guessed():
    with pytest.raises(G.GitHubMissing, match="no \\[github\\] section"):
        G.resolve_inputs(None, {"a": "github:CruiseData/X/a.csv"})


# ---------------------------------------------------------------- reader normalisation for older workbooks
@pytest.mark.parametrize("raw,expected", [("SA-0367", "367"), ("SAL-123", "123"), (19.0, "19"), (19, "19"), ("415", "415"),
                                          ("19.0", "19"), ("S472", "472"), ("Chk SW", "Chk SW"), (" Chk Sw ", "Chk Sw")])
def test_bottle_ids_compare_on_the_number(raw, expected):
    assert discrete.normalize_bottle_id(raw) == expected


# ---------------------------------------------------------------- config / pipeline behaviour (no network)
def test_year_without_profiler_file_is_reported_missing_and_nothing_is_written(tmp_path):
    cfg = load_config(ROOT / "config" / "cruises.toml")
    del cfg["years"]["2024"]["inputs"]["profiler_nc"]
    (tmp_path / "c.json").write_text(json.dumps(cfg), encoding="utf8")
    with pytest.raises(MissingInputError) as e:
        run_year(2024, tmp_path / "c.json", tmp_path)
    assert [m["role"] for m in e.value.missing] == ["profiler_nc"]
    assert "not configured" in e.value.missing[0]["path"]
    assert not any((tmp_path / "2024").glob("*.csv")) if (tmp_path / "2024").exists() else True


def test_earlier_years_inherit_variants_and_drop_windowed_ones():
    cfg = load_config(ROOT / "config" / "cruises.toml")
    v = year_variants(cfg, cfg["years"]["2024"].get("variants"), cfg["years"]["2024"].get("notebook_window_utc"))
    ids = {x["variant_id" if "variant_id" in x else "id"] for x in v}
    assert "nearest_upcast_depSM" in ids and "notebook_window_ascending_prDM" not in ids
    v25 = year_variants(cfg, cfg["years"]["2025"].get("variants"), cfg["years"]["2025"].get("notebook_window_utc"))
    assert "notebook_window_ascending_prDM" in {x["id"] for x in v25}


def test_every_configured_github_input_exists_in_the_cached_tree_if_available():
    cache = ROOT / "data" / "cache" / "github"
    trees = sorted(cache.glob("*/_tree.json")) if cache.is_dir() else []
    if not trees:
        pytest.skip("no cached GitHub tree yet (run scripts/run_year.py --check once)")
    paths = {e["path"] for t in trees for e in json.loads(t.read_text(encoding="utf8"))}   # every cached repository / commit
    cfg = load_config(ROOT / "config" / "cruises.toml")
    bad = [(y, r, v) for y, yc in cfg["years"].items() for r, v in {**yc.get("inputs", {}), **yc.get("oxygen", {}).get("inputs", {})}.items()
           if G.is_github_spec(v) and G.split_spec(v)[1] not in paths]
    assert not bad, bad


def test_named_sources_are_parsed_and_resolved_from_their_own_repository(tmp_path):
    assert G.split_spec("github:CruiseData/X/a.csv") == (None, "CruiseData/X/a.csv")
    assert G.split_spec("github[profile_indices]:RS03AXPS_profiles_2022.csv") == ("profile_indices", "RS03AXPS_profiles_2022.csv")
    assert not G.is_github_spec("data/raw/x.csv") and not G.is_github_spec("githubby:x")
    with pytest.raises(G.GitHubMissing, match="github_sources.nope"):
        G.resolve_inputs({None: src(tmp_path)}, {"a": "github[nope]:a.csv"})
    http = FakeHttp()
    srcs = {None: src(tmp_path / "d", http=http), "idx": G.Source("o/idx", "main", tmp_path / "i", http)}
    cfg = {"github": {"repo": "o/r", "cache_dir": str(tmp_path)}, "github_sources": {"idx": {"repo": "o/idx"}}}
    made = G.sources_from_config(cfg, tmp_path)
    assert made[None].repo == "o/r" and made["idx"].repo == "o/idx" and made["idx"].cache_dir == made[None].cache_dir
    assert srcs["idx"].repo == "o/idx"


def test_branch_is_resolved_with_git_ref_advertisement_before_the_api(tmp_path):
    sha_main, sha_tag = "a" * 40, "b" * 40
    adv = (f"001e# service=git-upload-pack\n0000{sha_main} refs/heads/main\n{sha_tag} refs/heads/mainline\n").encode()
    calls = []

    def http(url, api=False, timeout=0):
        calls.append(url)
        if url.endswith("info/refs?service=git-upload-pack"):
            return adv
        raise AssertionError("the API must not be used when the ref advertisement answers")
    assert G.Source("o/r", "main", tmp_path, http).commit() == sha_main     # 'main', not 'mainline'
    assert len(calls) == 1


def test_api_is_the_fallback_when_the_ref_advertisement_fails(tmp_path):
    def http(url, api=False, timeout=0):
        if api:
            return json.dumps({"sha": "c" * 40}).encode()
        raise OSError("no route")
    assert G.Source("o/r", "main", tmp_path, http).commit() == "c" * 40
