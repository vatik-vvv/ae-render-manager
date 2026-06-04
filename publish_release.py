"""
Create Windows release ZIP and upload to GitHub Releases (optional).

Requires GITHUB_TOKEN or GH_TOKEN with repo scope for upload.
Without a token, only builds the ZIP locally.

Usage:
  python publish_release.py
  python publish_release.py --version 1.1.0 --skip-upload
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
REPO = "vatik-vvv/ae-render-manager"
DEFAULT_VERSION = "1.1.1"


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def build_zip(version: str) -> Path:
    exe = ROOT / "dist" / "AERenderManager.exe"
    if not exe.is_file():
        raise FileNotFoundError(
            f"Missing {exe}. Run: pyinstaller main.spec --noconfirm"
        )
    assets = [
        (exe, "AERenderManager.exe"),
        (ROOT / "config.example.json", "config.example.json"),
        (ROOT / "RELEASE_INSTALL.txt", "RELEASE_INSTALL.txt"),
    ]
    for path, _ in assets:
        if not path.is_file():
            raise FileNotFoundError(path)

    zip_path = ROOT / f"AERenderManager-{version}-win64.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for src, arc in assets:
            print(f"  adding {arc} ({src.stat().st_size // 1024 // 1024} MB)...")
            zf.write(src, arcname=arc)
    print(f"Created {zip_path} ({zip_path.stat().st_size // 1024 // 1024} MB)")
    return zip_path


def _api(method: str, url: str, token: str, data: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers, method=method)
    with urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def upload_release(version: str, zip_path: Path, token: str) -> str:
    tag = f"v{version}"
    notes = f"""## After Effects Render Manager {version} (Windows)

Pre-built package — no Python or compile step required.

### Download
- **{zip_path.name}** — unzip and run `AERenderManager.exe`
- Copy `config.example.json` → `config.json` and set your `aerender_path`

### Changes in 1.1.1
- Fix renders writing 0 frames when proxy/RS forced Best Settings without output module
- Pass `-OMtemplate` when render settings are overridden; infer PNG/JPEG OM from output path
- Use queue by default on scan; proxy no longer overrides RS (keeps AE queue OM/path)
- Stop only kills aerender trees started by this app (not all After Effects instances)
- Queue row reorder during render keeps progress on the correct row
- Clearer failure diagnostics (log size, output folder, RS/OM hints)
- Fix `is_use_queue_rs` crash that aborted jobs immediately after the command line

### Changes in 1.1.0
- Fix app closing when queue finishes or Stop (no longer kills AERenderManager.exe)
- Optional sleep entire PC when queue completes (with confirmation)
- Richer Telegram notifications (AEP, comp, frames, timing, status)
- Multi-size icon for Explorer extra-large view
- Frame range / skip / proxy scan improvements
"""
    owner, name = REPO.split("/", 1)
    base = f"https://api.github.com/repos/{REPO}"

    try:
        rel = _api("GET", f"{base}/releases/tags/{tag}", token)
        release_id = rel["id"]
        print(f"Release {tag} already exists (id={release_id}), uploading asset…")
        for asset in rel.get("assets", []):
            if asset.get("name") == zip_path.name:
                _api("DELETE", f"{base}/releases/assets/{asset['id']}", token)
                print(f"Removed previous asset: {zip_path.name}")
    except HTTPError as err:
        if err.code != 404:
            raise
        rel = _api(
            "POST",
            f"{base}/releases",
            token,
            {
                "tag_name": tag,
                "name": tag,
                "body": notes,
                "draft": False,
            },
        )
        release_id = rel["id"]
        print(f"Created release {tag}")

    upload_url = (
        f"https://uploads.github.com/repos/{owner}/{name}/releases/{release_id}/assets"
        f"?name={zip_path.name}"
    )
    with open(zip_path, "rb") as handle:
        data = handle.read()
    req = Request(
        upload_url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/zip",
            "Content-Length": str(len(data)),
        },
        method="POST",
    )
    with urlopen(req, timeout=600) as resp:
        asset = json.loads(resp.read().decode("utf-8"))
    html_url = rel.get("html_url") or f"https://github.com/{REPO}/releases/tag/{tag}"
    print(f"Uploaded asset: {asset.get('browser_download_url', zip_path.name)}")
    return html_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--skip-upload", action="store_true")
    args = parser.parse_args()

    zip_path = build_zip(args.version)
    if args.skip_upload:
        print("Skip upload (--skip-upload).")
        return 0

    token = _token()
    if not token:
        print(
            "No GITHUB_TOKEN/GH_TOKEN — ZIP built only.\n"
            "Upload manually: GitHub → Releases → New release → attach zip.",
            file=sys.stderr,
        )
        return 0

    url = upload_release(args.version, zip_path, token)
    print(f"Release: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
