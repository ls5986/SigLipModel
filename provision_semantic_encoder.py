"""Explicit provisioning command. Does not train or activate a worker."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from huggingface_hub import snapshot_download

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
FILES = ["config.json", "config_sentence_transformers.json", "modules.json",
         "sentence_bert_config.json", "tokenizer.json", "tokenizer_config.json",
         "special_tokens_map.json", "vocab.txt", "1_Pooling/config.json",
         "model.safetensors"]

def provision(destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Use a new checkpoint directory; existing artifacts are not overwritten")
    source = Path(snapshot_download(repo_id=MODEL, revision=REVISION,
                  allow_patterns=FILES, token=False, max_workers=2))
    if not all((source / name).is_file() for name in FILES):
        raise ValueError("Checkpoint is incomplete")
    destination.mkdir(parents=True)
    for name in FILES:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    digest = hashlib.sha256()
    for path in sorted(p for p in destination.rglob("*") if p.is_file()):
        digest.update(path.relative_to(destination).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1048576), b""):
                digest.update(block)
    return {"model":MODEL, "revision":REVISION, "checkpoint_sha256":digest.hexdigest(),
            "directory":str(destination.resolve()), "trained":False}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination")
    print(json.dumps(provision(parser.parse_args().destination)))
