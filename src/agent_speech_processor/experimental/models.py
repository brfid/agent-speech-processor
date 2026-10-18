"""Explicit model installation and byte identity; inference is always offline."""
import importlib.metadata
import json
from pathlib import Path
import shutil
import urllib.request

from ..records import atomic, checksum, json_bytes, now

MODEL_ROOT = Path.home() / "Library/Application Support/agent-speech-processor/models"
MODELS = {
    "moss": ("OpenMOSS-Team/MOSS-Transcribe-Diarize",
             "704aa4a9c304e8520be88901e0d1960158ef5b15"),
    "qwen2-audio": ("mlx-community/Qwen2-Audio-7B-Instruct-4bit",
                    "c65570002626f41b4dc08b7b54f42f99f3e82e7f"),
    "gemma3n": ("mlx-community/gemma-3n-E2B-it-4bit",
                "66e7276cfc589073b4f92ebed58c05301f990222"),
}
RESERVE_BYTES = 8 * 1024**3


def runtime_versions():
    versions = {}
    for name in ("mlx-audio", "mlx", "mlx-lm", "mlx-vlm", "transformers", "numpy", "webrtcvad-wheels"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def install(name, root=MODEL_ROOT):
    """Download one pinned model, after checking its license and actual byte size."""
    from huggingface_hub import snapshot_download
    root = Path(root).expanduser().resolve()
    if "com~apple~CloudDocs" in root.parts or any((p / ".git").exists() for p in (root, *root.parents)):
        raise ValueError("Models belong outside iCloud and Git repositories")
    model_id, revision = MODELS[name]
    destination = root / name / revision
    receipt_path = destination / ".installation.json"
    if receipt_path.exists():
        return identity(destination)
    with urllib.request.urlopen(
            f"https://huggingface.co/api/models/{model_id}/revision/{revision}?blobs=true",
            timeout=60) as response:
        metadata = json.load(response)
    license_name = "gemma" if name == "gemma3n" else "apache-2.0"
    if metadata["sha"] != revision or metadata.get("cardData", {}).get("license") != license_name:
        raise ValueError("Model revision or expected license changed; inspect before downloading")
    files = [f for f in metadata["siblings"] if f["rfilename"].endswith(
        (".json", ".safetensors", ".txt", ".model", ".md", ".jinja"))]
    size = sum(f["size"] for f in files)
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < size + RESERVE_BYTES:
        raise RuntimeError(f"Need {size} model bytes plus {RESERVE_BYTES} free reserve bytes")
    print(json.dumps({"model": model_id, "revision": revision, "bytes": size,
                      "license": license_name, "destination": str(destination)}), flush=True)
    snapshot_download(model_id, revision=revision, local_dir=destination,
                      allow_patterns=[f["rfilename"] for f in files], max_workers=2)
    hashes = {}
    for entry in files:
        path = destination / entry["rfilename"]
        digest = checksum(path)
        if path.stat().st_size != entry["size"] or (
                entry.get("lfs", {}).get("sha256") and digest != entry["lfs"]["sha256"]):
            raise ValueError(f"Downloaded model checksum/size mismatch: {path}")
        hashes[entry["rfilename"]] = digest
    record = {"schema": 1, "model_id": model_id, "revision": revision,
              "license": license_name, "files": hashes, "bytes": size,
              "installed_at": now()}
    atomic(receipt_path, json_bytes(record), replace=False)
    return identity(destination)


def identity(folder):
    """Verify all pinned files once before a run. Receipt contents enter its recipe."""
    folder = Path(folder).expanduser().resolve()
    receipt = folder / ".installation.json"
    record = json.loads(receipt.read_text())
    if record.get("schema") != 1 or not record.get("files"):
        raise ValueError("Invalid model installation receipt")
    for relative, digest in record["files"].items():
        path = folder / relative
        if not path.resolve().is_relative_to(folder) or checksum(path) != digest:
            raise ValueError(f"Model file changed: {path}")
    return {"path": str(folder), "model_id": record["model_id"],
            "revision": record["revision"], "installation_sha256": checksum(receipt),
            "files": record["files"]}
