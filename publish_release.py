"""
Create Windows release ZIP + optional Inno installer; upload to GitHub Releases.

Requires GITHUB_TOKEN or GH_TOKEN with repo scope for upload.
Without a token, only builds artifacts locally.

Usage:
  python publish_release.py
  python publish_release.py --version 1.1.1 --skip-upload
  python publish_release.py --prune-tags
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
REPO = "vatik-vvv/ae-render-manager"
DEFAULT_VERSION = "1.1.1"
KEEP_TAG = f"v{DEFAULT_VERSION}"

RELEASE_NOTES = """## After Effects Render Manager {version} (Windows)

Pre-built Windows package — no Python required.

### Download
- **{setup_name}** — installer (recommended): unzip and run `Install.ps1`, or use the `.exe` setup if listed
- **{zip_name}** — portable (unzip and run `AERenderManager.exe` directly)

Edit `config.json` next to the exe: set `aerender_path` to your After Effects `aerender.exe`.

### Push from open After Effects
1. Save the `.aep`, open **Window → AE Render Manager** in AE, click **Send to AE Render Manager**.
2. The manager launches or raises and imports queue rows automatically.
3. AE scripting: **Allow Scripts to Write Files and Access Network** (for auto-launch).

### Highlights (1.1.1)
- Push render queue from open AE (ScriptUI panel + auto-launch/raise)
- Event-driven import (no background polling); scan still available
- Render fix: 0 frames when proxy forced Best Settings without output module
- `-OMtemplate` when RS overridden; infer PNG/JPEG OM from output path
- Stop kills only this app's aerender trees (not all AE instances)
- Queue row reorder during render; clearer failure diagnostics
- Telegram setup dialog with bot/chat ID instructions
- Sleep PC when queue finishes (no confirmation when option enabled)
- UI: header logo, zone hints, column header tooltips

### Older (1.1.0)
- Fix app closing when queue finishes or Stop
- Telegram queue summaries; multi-size icon; frame range / skip / proxy scan
"""


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


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


def find_installer(version: str) -> Path | None:
    for name in (
        f"AERenderManager-{version}-win64-setup.exe",
        f"AERenderManager-{version}-win64-setup.zip",
    ):
        path = ROOT / "release" / name
        if path.is_file():
            return path
    return None


def build_ps_setup_zip(version: str) -> Path:
    """Portable setup ZIP with Install.ps1 (fallback when Inno Setup is missing)."""
    exe = ROOT / "dist" / "AERenderManager.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"Missing {exe}")
    staging = ROOT / "release" / "_setup_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copy2(exe, staging / "AERenderManager.exe")
    shutil.copy2(ROOT / "config.example.json", staging / "config.example.json")
    shutil.copy2(ROOT / "RELEASE_INSTALL.txt", staging / "RELEASE_INSTALL.txt")
    shutil.copy2(ROOT / "installer" / "Install.ps1", staging / "Install.ps1")
    zip_path = ROOT / "release" / f"AERenderManager-{version}-win64-setup.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file in sorted(staging.iterdir()):
            print(f"  setup: {file.name}")
            zf.write(file, arcname=file.name)
    shutil.rmtree(staging)
    print(f"Created {zip_path} ({zip_path.stat().st_size // 1024 // 1024} MB)")
    return zip_path


def build_installer(version: str) -> Path | None:
    """Run Inno Setup if ISCC.exe is on PATH or in Program Files."""
    iss = ROOT / "installer" / "AERenderManager.iss"
    if not iss.is_file():
        print("No installer/AERenderManager.iss — skip installer.")
        return None
    candidates = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    iscc = next((p for p in candidates if p.is_file()), None)
    if iscc is None:
        print("Inno Setup 6 not found — building PowerShell setup ZIP.")
        (ROOT / "release").mkdir(exist_ok=True)
        return build_ps_setup_zip(version)
    (ROOT / "release").mkdir(exist_ok=True)
    print(f"Building Inno installer with {iscc}...")
    subprocess.run([str(iscc), str(iss)], check=True, cwd=ROOT)
    out = find_installer(version)
    if out:
        print(f"Created {out} ({out.stat().st_size // 1024 // 1024} MB)")
        return out
    return build_ps_setup_zip(version)


def _upload_asset(token: str, release_id: int, file_path: Path, content_type: str) -> None:
    owner, name = REPO.split("/", 1)
    base = f"https://api.github.com/repos/{REPO}"
    upload_url = (
        f"https://uploads.github.com/repos/{owner}/{name}/releases/{release_id}/assets"
        f"?name={file_path.name}"
    )
    with open(file_path, "rb") as handle:
        data = handle.read()
    req = Request(
        upload_url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": content_type,
            "Content-Length": str(len(data)),
        },
        method="POST",
    )
    with urlopen(req, timeout=600) as resp:
        asset = json.loads(resp.read().decode("utf-8"))
    print(f"Uploaded: {asset.get('browser_download_url', file_path.name)}")


def upload_release(version: str, zip_path: Path, setup_path: Path | None, token: str) -> str:
    tag = f"v{version}"
    zip_name = zip_path.name
    setup_name = setup_path.name if setup_path else "(installer not built)"
    notes = RELEASE_NOTES.format(version=version, zip_name=zip_name, setup_name=setup_name)
    base = f"https://api.github.com/repos/{REPO}"

    try:
        rel = _api("GET", f"{base}/releases/tags/{tag}", token)
        release_id = rel["id"]
        print(f"Release {tag} exists (id={release_id}), updating…")
        _api("PATCH", f"{base}/releases/{release_id}", token, {"body": notes, "name": tag})
        for asset in rel.get("assets", []):
            _api("DELETE", f"{base}/releases/assets/{asset['id']}", token)
            print(f"Removed old asset: {asset.get('name')}")
    except HTTPError as err:
        if err.code != 404:
            raise
        rel = _api(
            "POST",
            f"{base}/releases",
            token,
            {"tag_name": tag, "name": tag, "body": notes, "draft": False},
        )
        release_id = rel["id"]
        print(f"Created release {tag}")

    _upload_asset(token, release_id, zip_path, "application/zip")
    if setup_path and setup_path.is_file():
        ctype = (
            "application/vnd.microsoft.portable-executable"
            if setup_path.suffix.lower() == ".exe"
            else "application/zip"
        )
        _upload_asset(token, release_id, setup_path, ctype)

    html_url = rel.get("html_url") or f"https://github.com/{REPO}/releases/tag/{tag}"
    return html_url


def prune_old_releases(token: str, keep_tag: str = KEEP_TAG) -> None:
    """Delete GitHub releases and tags except keep_tag."""
    base = f"https://api.github.com/repos/{REPO}"
    releases = _api("GET", f"{base}/releases?per_page=100", token)
    for rel in releases:
        tag = rel.get("tag_name", "")
        if tag == keep_tag:
            continue
        rid = rel["id"]
        _api("DELETE", f"{base}/releases/{rid}", token)
        print(f"Deleted release: {tag}")

    tags = _api("GET", f"{base}/tags?per_page=100", token)
    for tag_info in tags:
        name = tag_info.get("name", "")
        if name == keep_tag:
            continue
        # Delete remote tag via git ref API
        ref = f"tags/{name}"
        try:
            _api("DELETE", f"{base}/git/refs/{ref}", token)
            print(f"Deleted tag: {name}")
        except HTTPError as exc:
            print(f"Could not delete tag {name}: {exc.code}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--skip-installer", action="store_true")
    parser.add_argument("--zip-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prune-tags", action="store_true", help="Remove old GitHub releases/tags")
    args = parser.parse_args()

    if args.prune_tags:
        token = _token()
        if not token:
            print("GITHUB_TOKEN required for --prune-tags", file=sys.stderr)
            return 1
        prune_old_releases(token, keep_tag=f"v{args.version}")
        return 0

    zip_path = build_zip(args.version)
    setup_path = None
    if not args.skip_installer:
        setup_path = build_installer(args.version)

    if args.skip_upload:
        print("Skip upload (--skip-upload).")
        return 0

    token = _token()
    if not token:
        print(
            "No GITHUB_TOKEN/GH_TOKEN — artifacts built only.\n"
            "Upload with: gh release upload … or set GITHUB_TOKEN.",
            file=sys.stderr,
        )
        return 0

    url = upload_release(args.version, zip_path, setup_path, token)
    prune_old_releases(token, keep_tag=f"v{args.version}")
    print(f"Release: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
