"""OOINet request helpers: status handling, resumable URLs, and 'nothing left behind while queued' (no network)."""
import json

import pytest

from rca_sal import ooinet


@pytest.mark.parametrize("status,done", [
    ({"20250818T000000-20250819T000000": "complete"}, True),
    ({"a": "complete", "b": {"code": 404, "message": "All data removed by annotation mask"}}, True),   # a sub-request with no data is final
    ({"a": "complete", "b": "running"}, False),
    ({}, False),
    (None, False),
])
def test_finished_means_every_sub_request_is_complete_or_failed(status, done):
    assert ooinet._finished(status) is done


def test_status_is_checked_once_even_with_zero_wait(monkeypatch):
    calls = []
    monkeypatch.setattr(ooinet, "status_once", lambda url: calls.append(url) or None)
    monkeypatch.setattr(ooinet, "final_once", lambda url: True)
    with pytest.raises(ooinet.OoinetError, match="has not completed"):
        ooinet.wait_complete("https://x/y", timeout_s=0)
    assert len(calls) == 1
    monkeypatch.setattr(ooinet, "status_once", lambda url: {"a": "complete"})
    assert ooinet.wait_complete("https://x/y", timeout_s=0) == {"a": "complete"}


def test_chunks_complete_is_not_enough_until_status_txt_says_complete(monkeypatch):
    """status.json can be all 'complete' while OOINet is still merging the chunks into the final .nc."""
    monkeypatch.setattr(ooinet, "status_once", lambda url: {"a": "complete"})
    monkeypatch.setattr(ooinet, "final_once", lambda url: False)
    with pytest.raises(ooinet.OoinetError, match="has not completed"):
        ooinet.wait_complete("https://x/y", timeout_s=0)


@pytest.mark.parametrize("body,done", [(b"complete\n", True), (b"Complete", True), (b"processing", False)])
def test_final_marker(monkeypatch, body, done):
    monkeypatch.setattr(ooinet, "_get", lambda url, **kw: body)
    assert ooinet.final_once("https://x/y/") is done


def test_request_urls_are_rebuilt_from_the_timestamp_and_account_folder():
    cat, asy = ooinet.request_urls("RS03AXPS-SF03A-2A-CTDPFA302", "streamed", "ctdpf_sbe43_sample", "20261015T180708931Z", "someone@example.org")
    assert cat.endswith("/ooi/someone@example.org/20261015T180708931Z-RS03AXPS-SF03A-2A-CTDPFA302-streamed-ctdpf_sbe43_sample/catalog.html")
    assert asy == "https://downloads.oceanobservatories.org/async_results/someone@example.org/20261015T180708931Z-RS03AXPS-SF03A-2A-CTDPFA302-streamed-ctdpf_sbe43_sample"


def test_account_folder_comes_from_the_response():
    r = {"allURLs": ["https://t/thredds/catalog/ooi/a@b.org/x/catalog.html", "https://d/async_results/a@b.org/x"]}
    assert ooinet.async_user(r) == "a@b.org"


def test_missing_credentials_are_an_explicit_error(monkeypatch):
    monkeypatch.delenv("OOI_API_USERNAME", raising=False)
    monkeypatch.delenv("OOI_API_TOKEN", raising=False)
    with pytest.raises(ooinet.OoinetError, match="not set"):
        ooinet._auth_header()


def test_queued_request_leaves_no_folder_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(ooinet, "status_once", lambda url: None)
    with pytest.raises(ooinet.OoinetError):
        ooinet.download({"allURLs": ["https://t/catalog.html", "https://a/b"]}, tmp_path, "20260101T000000000Z", wait_timeout_s=0)
    assert not (tmp_path / "20260101T000000000Z").exists()


def test_completed_request_is_saved_unchanged(tmp_path, monkeypatch):
    payload = {"x.nc": b"\x89HDF-bytes", "x_provenance.json": b'{"p": 1}', "skip.txt": b"not saved"}
    monkeypatch.setattr(ooinet, "status_once", lambda url: {"a": "complete"})
    monkeypatch.setattr(ooinet, "final_once", lambda url: True)
    monkeypatch.setattr(ooinet, "_thredds_files", lambda cat: [f"ooi/u/stamp/{n}" for n in payload])
    monkeypatch.setattr(ooinet, "_get", lambda url, auth=False, timeout=60: payload[url.rsplit("/", 1)[-1]])
    out = ooinet.download({"allURLs": ["https://t/catalog.html", "https://a/b"]}, tmp_path, "stamp", wait_timeout_s=0)
    assert (out / "x.nc").read_bytes() == payload["x.nc"] and (out / "x_provenance.json").read_bytes() == payload["x_provenance.json"]
    assert not (out / "skip.txt").exists() and json.loads((out / "status.json").read_text()) == {"a": "complete"}
