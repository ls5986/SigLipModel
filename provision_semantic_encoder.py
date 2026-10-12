"""Pinned text checkpoint provisioning and lightweight runtime integrity checks."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
FILES = ["config.json", "config_sentence_transformers.json", "modules.json",
         "sentence_bert_config.json", "tokenizer.json", "tokenizer_config.json",
         "special_tokens_map.json", "vocab.txt", "1_Pooling/config.json",
         "model.safetensors"]
DIRECTORY = Path(__file__).resolve().parent / "artifacts" / "semantic_encoder" / REVISION

def checkpoint_digest(directory):
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(directory).rglob("*") if p.is_file()):
        if path.is_symlink():
            raise ValueError("Checkpoint must contain copied regular files")
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1048576), b""):
                digest.update(block)
    return digest.hexdigest()

def manifest_path(directory):
    return Path(directory).with_name(Path(directory).name + ".manifest.json")

def verify(directory=DIRECTORY):
    directory = Path(directory)
    record = json.loads(manifest_path(directory).read_text())
    if record["model"] != MODEL or record["revision"] != REVISION:
        raise ValueError("Text encoder identity changed")
    if not all((directory / name).is_file() for name in FILES):
        raise ValueError("Text checkpoint is incomplete")
    if checkpoint_digest(directory) != record["checkpoint_sha256"]:
        raise ValueError("Text checkpoint hash changed")
    return {**record, "directory":str(directory.resolve())}

def provision(directory=DIRECTORY):
    directory = Path(directory)
    if directory.exists():
        return verify(directory)
    from huggingface_hub import snapshot_download
    source = Path(snapshot_download(repo_id=MODEL, revision=REVISION,
                  allow_patterns=FILES, token=False, max_workers=2))
    if not all((source / name).is_file() for name in FILES):
        raise ValueError("Text checkpoint download is incomplete")
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory.parent, prefix="provision-") as temporary:
        stage = Path(temporary) / "checkpoint"
        stage.mkdir()
        for name in FILES:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
        record = {"model":MODEL, "revision":REVISION,
                  "checkpoint_sha256":checkpoint_digest(stage), "trained":False}
        stage.rename(directory)
        manifest_path(directory).write_text(json.dumps(record, indent=2)+"\n")
    return verify(directory)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--destination", type=Path, dest="destination_option")
    args = parser.parse_args()
    print(json.dumps(provision(args.destination_option or args.destination or DIRECTORY)))
