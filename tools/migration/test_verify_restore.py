import hashlib
import tempfile
import unittest
from pathlib import Path

import httpx
from verify_restore import restore_record


class RestoreTests(unittest.TestCase):
    def test_reassembles_exact_file_from_remote_chunks(self):
        parts = [b"first", b"second"]
        row = {"bytes": 11, "sha256": hashlib.sha256(b"firstsecond").hexdigest(), "parts": [
            {"key": str(i), "offset": sum(len(v) for v in parts[:i]), "length": len(blob),
             "sha256": hashlib.sha256(blob).hexdigest()} for i, blob in enumerate(parts)
        ]}
        with tempfile.TemporaryDirectory() as directory, httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=parts[int(request.url.path.rsplit("/", 1)[1])])
        )) as http:
            target = Path(directory) / "restored.bin"
            restore_record(http, row, target)
            self.assertEqual(target.read_bytes(), b"firstsecond")

    def test_rejects_file_hash_mismatch(self):
        row = {"bytes": 4, "sha256": "0"*64, "parts": [
            {"key": "one", "offset": 0, "length": 4, "sha256": hashlib.sha256(b"data").hexdigest()},
        ]}
        with tempfile.TemporaryDirectory() as directory, httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"data")
        )) as http:
            with self.assertRaisesRegex(ValueError, "file hash"):
                restore_record(http, row, Path(directory) / "restored.bin")


if __name__ == "__main__":
    unittest.main()
