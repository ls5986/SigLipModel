"""Reproducible CPU worker build. No database writes, training or paid calls."""
import subprocess
import sys

subprocess.run([sys.executable, "-m", "pip", "install", "--index-url",
                "https://download.pytorch.org/whl/cpu", "torch==2.6.0"], check=True)
subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                "requirements-label-worker.txt"], check=True)
from provision_semantic_encoder import provision
import json
print("Semantic checkpoint provisioned: " + json.dumps(provision()), flush=True)

