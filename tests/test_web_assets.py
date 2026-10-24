"""Every static asset the pages load must exist, be precached by the service worker
(where it is part of the shell), and be shipped in the wheel (setuptools package-data).

The UI used to be one web/app.js, which the `web/*.js` package-data glob covered.
Splitting it into web/js/*.js would have shipped a wheel with no UI scripts if
the glob had not been extended, and nothing noticed; this keeps that from recurring.
"""
import fnmatch
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "solar_monitor" / "web"
PAGES = ("index.html", "login.html", "login-setup.html")


def _package_globs():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return cfg["tool"]["setuptools"]["package-data"]["solar_monitor"]


def _covered(rel: str, globs) -> bool:
    # setuptools globs do not cross directories: `web/*.js` is one level only.
    return any(fnmatch.fnmatchcase(rel, g) and rel.count("/") == g.count("/") for g in globs)


def _page_assets():
    out = set()
    for page in PAGES:
        html = (WEB / page).read_text()
        out |= {m for m in re.findall(r'(?:src|href)="/web/([^"?#]+)', html)}
    return sorted(out)


def test_every_asset_the_pages_load_exists_and_is_packaged():
    globs = _package_globs()
    missing, unpackaged = [], []
    for rel in _page_assets():
        if not (WEB / rel).is_file():
            missing.append(rel)
        elif not _covered("web/" + rel, globs):
            unpackaged.append(rel)
    assert not missing, f"pages reference files that do not exist: {missing}"
    assert not unpackaged, f"files the pages load are not in package-data: {unpackaged}"


def test_ui_scripts_load_in_numeric_order_and_the_service_worker_precaches_them():
    html = (WEB / "index.html").read_text()
    tags = re.findall(r'<script src="/web/js/([^"?]+)\?v=(\d+)"', html)
    names = [n for n, _ in tags]
    assert names, "index.html loads no /web/js/ scripts"
    assert names == sorted(names), "UI scripts must be loaded in numeric order (they share one global scope)"
    assert len({v for _, v in tags}) == 1, "all UI scripts should carry the same cache-buster"
    on_disk = sorted(p.name for p in (WEB / "js").glob("*.js"))
    assert names == on_disk, "every web/js/*.js must be loaded by index.html, and vice versa"
    sw = (WEB / "sw.js").read_text()
    for n in names:
        assert f"'/web/js/{n}'" in sw, f"sw.js does not precache {n}"
    version = tags[0][1]
    assert f"app{version}" in sw, "bump CACHE_VERSION in sw.js together with the ?v= cache-buster"
