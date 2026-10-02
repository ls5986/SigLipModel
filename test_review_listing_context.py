"""Run the real JavaScript renderer regression as part of the Python checks."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_listing_context_renders_without_undefined_outer_variables():
    node = shutil.which('node')
    if not node: pytest.skip('Node.js is required for UI runtime regression')
    result = subprocess.run([node,'--test',str(Path(__file__).with_suffix('.cjs'))],
                            capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
