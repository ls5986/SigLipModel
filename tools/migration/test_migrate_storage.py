import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import migrate_storage as migration


class StorageMigrationTests(unittest.TestCase):
    def test_chunked_artifact_reassembles_exactly(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.bin"
            blob = b"abc123" * 1200000
            path.write_bytes(blob)
            digest = migration.digest_file(path)
            parts = migration.file_parts(path, digest, False)
            self.assertEqual(len(parts), 2)
            reconstructed = b"".join(blob[p["offset"]:p["offset"]+p["length"]] for p in parts)
            self.assertEqual(hashlib.sha256(reconstructed).hexdigest(), digest)
            self.assertTrue(all(p["length"] <= migration.CHUNK for p in parts))

    def test_photo_key_matches_database_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image.jpg"
            path.write_bytes(b"fixture-bytes")
            digest = migration.digest_file(path)
            parts = migration.file_parts(path, digest, True)
            self.assertEqual(parts[0]["key"], "photos/" + digest + ".jpg")

    def test_download_verifies_bytes_not_just_object_existence(self):
        blob = b"verified"
        part = {"key": "photos/example.jpg", "sha256": hashlib.sha256(blob).hexdigest(), "length": len(blob)}
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=blob))) as http:
            migration.verify_object(http, part)
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"corrupt!"))) as http:
            with self.assertRaises(ValueError):
                migration.verify_object(http, part)
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403))) as http:
            with self.assertRaises(RuntimeError):
                migration.verify_object(http, part)

    def test_missing_storage_key_fails_before_network(self):
        with patch("migrate_storage.dotenv_values", return_value={}), patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "key is missing"):
                migration.client()

    def test_publishable_key_rejected_before_network(self):
        with patch("migrate_storage.dotenv_values", return_value={
            "SUPABASE_MIGRATION_STORAGE_KEY": "sb_publishable_test_not_real",
        }), patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "publishable"):
                migration.client()

    def test_upload_and_duplicate_both_require_download_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "photo.jpg"
            blob = b"photo"
            path.write_bytes(blob)
            part = migration.file_parts(path, migration.digest_file(path), True)[0]
            for status in (201, 409):
                methods = []

                def respond(request):
                    methods.append(request.method)
                    if request.method == "POST":
                        return httpx.Response(status)
                    return httpx.Response(200, content=blob)

                with httpx.Client(transport=httpx.MockTransport(respond)) as http:
                    self.assertEqual(migration.upload_part(http, path, part), (part["key"], part["sha256"]))
                self.assertEqual(methods, ["POST", "GET"])


if __name__ == "__main__":
    unittest.main()
