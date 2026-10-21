"""Build-only download of checksum-pinned Docling models; no runtime network."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            result.update(chunk)
    return result.hexdigest()


def main() -> None:
    config = json.loads(Path("config/parser-context-resources.json").read_text())
    for model in config["models"]:
        for item in model["files"]:
            relative = Path(model["repo_id"].replace("/", "--")) / item["path"]
            cache = Path("/root/.cache/align-parser") / relative
            target = Path("/opt/docling-models") / relative
            cache.parent.mkdir(parents=True, exist_ok=True)
            if not cache.is_file() or digest(cache) != item["sha256"]:
                url = f"https://huggingface.co/{model['repo_id']}/resolve/{model['revision']}/{item['path']}?download=true"
                for attempt in range(3):
                    try:
                        print(f"Downloading pinned resource: {relative}", flush=True)
                        with urllib.request.urlopen(url, timeout=30) as source, cache.open("wb") as output:  # noqa: S310 -- fixed HTTPS model registry
                            shutil.copyfileobj(source, output, 64 * 1024)
                        if cache.stat().st_size != item["size_bytes"] or digest(cache) != item["sha256"]:
                            raise ValueError(f"resource checksum mismatch: {relative}")
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(2)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cache, target)
    ocr_files = []
    for language in config["ocr"]["languages"]:
        path = Path(f"/usr/share/tesseract-ocr/5/tessdata/{language}.traineddata")
        ocr_files.append({"path": str(path), "sha256": digest(path)})
    for expected in config["ocr"].get("artifacts", []):
        if digest(Path(expected["path"])) != expected["sha256"]:
            raise ValueError("OCR traineddata checksum mismatch")
    if subprocess.check_output(["tesseract", "--version"], text=True).splitlines()[0] != "tesseract 5.5.0":  # noqa: S603, S607 -- build image executable
        raise ValueError("OCR executable version does not match the manifest")
    Path("/opt/docling-models/ocr-receipt.json").write_text(json.dumps(ocr_files, sort_keys=True))
    print("Pinned model resources verified; OCR receipt written.")


if __name__ == "__main__":
    main()
