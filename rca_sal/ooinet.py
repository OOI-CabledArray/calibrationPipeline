"""OOINet M2M asynchronous requests. Downloaded files are stored unchanged in data/raw/ooinet/<request timestamp>/.
Credentials are read from OOI_API_USERNAME and OOI_API_TOKEN and sent to the OOINet host only.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

M2M = "https://ooinet.oceanobservatories.org/api/m2m/12576/sensor/inv"
THREDDS_HOST = "https://opendap.oceanobservatories.org"


class OoinetError(RuntimeError):
    pass


def _auth_header() -> dict:
    u, t = os.environ.get("OOI_API_USERNAME"), os.environ.get("OOI_API_TOKEN")
    if not (u and t):
        raise OoinetError("OOI_API_USERNAME / OOI_API_TOKEN are not set in the environment")
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{t}".encode()).decode()}


def _get(url: str, auth: bool = False, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers=_auth_header() if auth else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def request_stamp(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}Z"


def submit(ref_des: str, method: str, stream: str, begin: str, end: str) -> dict:
    """Submit an async NetCDF request; returns the M2M response (allURLs, requestUUID, ...)."""
    site, node, sensor = ref_des.split("-", 2)
    q = (f"beginDT={begin}&endDT={end}&format=application/netcdf&include_provenance=true&include_annotations=true")
    url = f"{M2M}/{site}/{node}/{sensor}/{method}/{stream}?{q}"
    try:
        return json.loads(_get(url, auth=True))
    except urllib.error.HTTPError as e:
        raise OoinetError(f"OOINet request failed: HTTP {e.code}") from None


def status_once(async_url: str) -> dict | None:
    """The request's status.json if OOINet has produced it, else None (still queued or running)."""
    try:
        return json.loads(_get(async_url.rstrip("/") + "/status.json"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise OoinetError(f"status check failed: HTTP {e.code}") from None


def final_once(async_url: str) -> bool:
    """True once OOINet has written status.txt ("complete"). status.json reports the time chunks; the chunks are merged into the
    final .nc (and the provenance and annotation files written) after that, so a download started on status.json alone can get a
    half-written file."""
    try:
        return _get(async_url.rstrip("/") + "/status.txt").strip().lower() == b"complete"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise OoinetError(f"status check failed: HTTP {e.code}") from None


def _finished(status: dict | None) -> bool:
    vals = list((status or {}).values())
    return bool(vals) and all(v == "complete" or isinstance(v, dict) for v in vals)


def wait_complete(async_url: str, poll_s: float = 20, timeout_s: float = 3600) -> dict:
    """Poll until every sub-request in <async_url>/status.json is complete (or failed) and status.txt says complete."""
    t0 = time.time()
    while True:
        status = status_once(async_url)
        if _finished(status) and final_once(async_url):
            return status
        if time.time() - t0 >= timeout_s:
            raise OoinetError("OOINet has not completed the request yet")
        time.sleep(min(poll_s, max(timeout_s - (time.time() - t0), 0)))


def async_user(response: dict) -> str:
    """The OOINet account folder name a request was filed under, read from the M2M response's own URLs."""
    m = re.search(r"/async_results/([^/]+)/", response["allURLs"][1])
    if not m:
        raise OoinetError("cannot find the async-results account folder in the OOINet response")
    return m.group(1)


def request_urls(ref_des: str, method: str, stream: str, stamp: str, user: str) -> list[str]:
    """The catalog and async-result URLs of an earlier request, rebuilt from its timestamp (for resuming).

    `user` is the account folder OOINet filed the request under (see async_user); it need not equal OOI_API_USERNAME.
    """
    name = f"{stamp}-{ref_des}-{method}-{stream}"
    return [f"{THREDDS_HOST}/thredds/catalog/ooi/{user}/{name}/catalog.html",
            f"https://downloads.oceanobservatories.org/async_results/{user}/{name}"]


def _thredds_files(catalog_url: str) -> list[str]:
    cat_xml = catalog_url.replace("catalog.html", "catalog.xml")
    root = ET.fromstring(_get(cat_xml))
    ns = {"c": "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"}
    return [d.attrib["urlPath"] for d in root.iter("{%s}dataset" % ns["c"]) if "urlPath" in d.attrib]


def download(response: dict, out_root: str | Path, stamp: str, wait_timeout_s: float = 3600) -> Path:
    """Wait for a request to finish, then save every file of it unchanged into out_root/<stamp>/.

    The folder is only created once the request is complete, so a request that is still queued leaves nothing behind.
    """
    thredds_cat, async_url = response["allURLs"][0], response["allURLs"][1]
    status = wait_complete(async_url, timeout_s=wait_timeout_s)
    out = Path(out_root) / stamp
    out.mkdir(parents=True, exist_ok=False)
    (out / "status.json").write_text(json.dumps(status, indent=2), encoding="utf8")
    for url_path in _thredds_files(thredds_cat):
        name = url_path.rsplit("/", 1)[-1]
        if re.search(r"\.(nc|json)$", name) and name != "status.json":
            (out / name).write_bytes(_get(f"{THREDDS_HOST}/thredds/fileServer/{url_path}", timeout=600))
    return out
