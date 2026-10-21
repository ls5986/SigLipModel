"""Code is portable; private datasets, models and credentials stay outside Git."""
import os
from pathlib import Path

from dotenv import load_dotenv

CODE_ROOT = Path(__file__).resolve().parent
ENVIRONMENT = os.environ.get("SIGLIP_ENV", "development").strip().casefold()
if not ENVIRONMENT or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in ENVIRONMENT):
    raise ValueError("SIGLIP_ENV must contain only letters, numbers, underscores, or hyphens")
load_dotenv(CODE_ROOT / f".env.{ENVIRONMENT}", override=False)
load_dotenv(CODE_ROOT / ".env", override=False)


def configured_path(name, default):
    value = os.environ.get(name, "").strip()
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path.resolve()


DATA_ROOT = configured_path("ACQ_DATA_ROOT", Path.home() / ".siglipmodel")
EVIDENCE_ROOT = configured_path("ACQ_EVIDENCE_ROOT", DATA_ROOT / "evidence")
