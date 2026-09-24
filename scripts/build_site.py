"""Build the GitHub Pages demo with Python's standard library only."""

import hashlib
import html
import json
import shutil
from pathlib import Path


def build_site(root: Path) -> Path:
    root = root.resolve()
    data = root / "demo/data"
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    for filename, expected in manifest["files"].items():
        path = (data / filename).resolve()
        if not path.is_relative_to(data.resolve()):
            raise ValueError("Snapshot manifest contains an invalid path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Demo snapshot checksum mismatch: {filename}")
    destination = (root / "_site").resolve()
    if destination.parent != root or destination.name != "_site":
        raise ValueError("Refusing to replace anything outside the generated _site folder")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir()
    web = root / "src/fleetscope/web"
    shutil.copytree(web, destination / "static")
    page = (web / "index.html").read_text(encoding="utf-8")
    project = json.loads((root / "demo/project.json").read_text(encoding="utf-8"))
    repository = html.escape(project["repository_url"], quote=True)
    page = page.replace(
        '<html lang="en">', f'<html lang="en" data-mode="static" data-repository="{repository}">'
    )
    page = page.replace(
        'id="project-link" href="/docs">API docs ↗',
        f'id="project-link" href="{repository}">View on GitHub ↗',
    )
    page = page.replace('href="/api/quality"', 'href="./data/quality.json"')
    page = page.replace('href="/api/metadata"', 'href="./data/metadata.json"')
    (destination / "index.html").write_text(page, encoding="utf-8")
    (destination / "static/index.html").unlink()
    shutil.copytree(data, destination / "data")
    (destination / ".nojekyll").write_text("", encoding="utf-8")
    return destination


if __name__ == "__main__":
    print(build_site(Path(__file__).resolve().parents[1]))
