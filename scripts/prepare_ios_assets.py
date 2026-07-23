#!/usr/bin/env python3
"""Convert local/rendered HTML assets into an iOS Asset Catalog staging directory."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image


RASTER_PASSTHROUGH = {".png", ".jpg", ".jpeg"}
RASTER_CONVERT = {".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff"}
VECTOR = {".svg", ".pdf"}
FONT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2"}


def asset_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "asset"
    if cleaned[0].isdigit():
        cleaned = f"asset_{cleaned}"
    return cleaned[:80]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_imageset(catalog: Path, name: str, filename: str, data: bytes, vector: bool) -> Path:
    folder = catalog / f"{name}.imageset"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(data)
    contents = {
        "images": [{"filename": filename, "idiom": "universal", "scale": "1x"}],
        "info": {"author": "xcode", "version": 1},
    }
    if vector:
        contents["properties"] = {"preserves-vector-representation": True}
    (folder / "Contents.json").write_text(json.dumps(contents, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return folder


def decode_data_uri(value: str):
    match = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", value, re.DOTALL)
    if not match:
        return None
    mime, encoded, payload = match.groups()
    raw = base64.b64decode(payload) if encoded else unquote(payload).encode("utf-8")
    extension = {
        "image/svg+xml": ".svg", "image/png": ".png", "image/jpeg": ".jpg",
        "image/webp": ".webp", "image/gif": ".gif",
    }.get(mime or "", "")
    return raw, extension, mime


def local_path_for(url: str, source_root: Path) -> Path | None:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme in {"http", "https"}:
        candidate = source_root / unquote(parsed.path).lstrip("/")
        return candidate if candidate.is_file() else None
    candidate = source_root / unquote(parsed.path or url)
    return candidate if candidate.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("render_tree", type=Path)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--catalog-name", default="GeneratedAssets.xcassets")
    args = parser.parse_args()

    data = json.loads(args.render_tree.read_text(encoding="utf-8"))
    source_root = args.source_root.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    catalog = out_dir / args.catalog_name
    catalog.mkdir(parents=True, exist_ok=True)
    (catalog / "Contents.json").write_text(json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2) + "\n", encoding="utf-8")
    fonts_dir = out_dir / "Fonts"

    converted = []
    deferred = []
    native = []
    by_hash = {}

    def add_bytes(raw: bytes, extension: str, suggested: str, source: str):
        content_hash = digest(raw)
        if content_hash in by_hash:
            converted.append({"source": source, "assetName": by_hash[content_hash], "deduplicated": True})
            return
        name = asset_name(suggested)
        suffix = 2
        existing_names = {item.get("assetName") for item in converted}
        base = name
        while name in existing_names:
            name = f"{base}_{suffix}"
            suffix += 1
        output_extension = extension.lower()
        output = raw
        vector = output_extension in VECTOR
        if output_extension in RASTER_CONVERT:
            import io
            image = Image.open(io.BytesIO(raw))
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            output = buffer.getvalue()
            output_extension = ".png"
        if output_extension not in RASTER_PASSTHROUGH | VECTOR:
            deferred.append({"source": source, "reason": f"unsupported-or-animated-format:{extension or 'unknown'}"})
            return
        filename = f"{name}{output_extension}"
        folder = write_imageset(catalog, name, filename, output, vector)
        by_hash[content_hash] = name
        converted.append({
            "source": source,
            "assetName": name,
            "output": str(folder.relative_to(out_dir)),
            "format": output_extension.lstrip("."),
            "vector": vector,
            "sha256": content_hash,
            "deduplicated": False,
        })

    for node in data.get("nodes") or []:
        details = node.get("assetDetails") or {}
        kind = details.get("kind")
        source_id = node.get("runtimeId") or "asset"
        if kind == "inline-svg" and details.get("markup"):
            add_bytes(details["markup"].encode("utf-8"), ".svg", source_id, f"inline-svg:{node.get('selector')}")
            continue
        value = details.get("url")
        if kind == "css-background":
            background = details.get("value") or ""
            if "gradient(" in background:
                native.append({"nodeId": source_id, "kind": "native-gradient", "value": background})
            urls = re.findall(r"url\((?:['\"])?(.*?)(?:['\"])?\)", background)
            value = urls[0] if urls else None
        if not value:
            continue
        decoded = decode_data_uri(value)
        if decoded:
            raw, extension, mime = decoded
            add_bytes(raw, extension, source_id, f"data-uri:{mime}")
            continue
        parsed = urlparse(value)
        path = local_path_for(value, source_root)
        if path and path.is_file():
            add_bytes(path.read_bytes(), path.suffix, path.stem or source_id, str(path))
        elif parsed.scheme in {"http", "https"}:
            deferred.append({"source": value, "nodeId": source_id, "reason": "remote-requires-explicit-download-and-license-check"})
        else:
            deferred.append({"source": value, "nodeId": source_id, "reason": "local-file-not-found"})

    fonts = []
    for path in source_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in FONT_EXTENSIONS:
            continue
        fonts_dir.mkdir(parents=True, exist_ok=True)
        destination = fonts_dir / path.name
        shutil.copy2(path, destination)
        fonts.append({"source": str(path), "output": str(destination.relative_to(out_dir)), "requiresTargetMembership": True, "requiresInfoPlistRegistration": path.suffix.lower() in {".ttf", ".otf"}})

    manifest = {
        "schemaVersion": "ios-asset-preparation-1.0",
        "sourceRenderTree": str(args.render_tree.resolve()),
        "assetCatalog": str(catalog),
        "converted": converted,
        "nativeMappings": native,
        "fonts": fonts,
        "deferred": deferred,
        "summary": {
            "converted": len([item for item in converted if not item.get("deduplicated")]),
            "deduplicated": sum(bool(item.get("deduplicated")) for item in converted),
            "nativeMappings": len(native),
            "fonts": len(fonts),
            "deferred": len(deferred),
        },
    }
    manifest_path = out_dir / "asset-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(manifest_path), **manifest["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
