"""Run the loopback-only image-first Studio using configured private data paths."""
import io
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageOps

VERSION = Path(__file__).resolve().parent

import review_server

original_create_server = review_server.create_server


def create_server(port, app):
    server = original_create_server(port, app)
    parent = server.RequestHandlerClass

    @lru_cache(maxsize=64)
    def thumbnail(path, modified, size):
        with Image.open(path) as original:
            if original.width * original.height > 40_000_000:
                raise ValueError("Image dimensions exceed thumbnail limit")
            image = ImageOps.exif_transpose(original)
            image.thumbnail((240, 180))
            output = io.BytesIO()
            image.convert("RGB").save(output, "JPEG", quality=70)
            return output.getvalue()

    class Handler(parent):
        def do_GET(self):
            path = urlparse(self.path).path
            if path not in {"/", "/advanced", "/api/studio/thumbnail"}:
                return super().do_GET()
            if not self.authorized_host():
                self.json(403, {"error": "Loopback host required"})
                return
            if path in {"/", "/advanced"}:
                self.send(200, (VERSION / ("review_ui.html" if path == "/" else "advanced_ui.html")).read_bytes(), "text/html; charset=utf-8")
                return
            try:
                identifier = parse_qs(urlparse(self.path).query).get("id", [""])[0]
                file = app.get_studio().store.image_path(identifier)
                info = file.stat()
                self.send(200, thumbnail(file, info.st_mtime_ns, info.st_size), "image/jpeg")
            except (ValueError, OSError):
                self.json(404, {"error": "Photo preview unavailable"})
    server.RequestHandlerClass = Handler
    return server


review_server.create_server = create_server

if __name__ == "__main__":
    review_server.main()

