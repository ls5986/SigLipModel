import hashlib
import io

import httpx
from PIL import Image

from backfill_validation_media import normalize_image, object_key, storage_objects, storage_upload


def test_normalize_image_creates_bounded_verified_jpeg():
    source = io.BytesIO()
    Image.new("RGBA", (4000, 2000), "navy").save(source, "PNG")
    result = normalize_image(source.getvalue())
    with Image.open(io.BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.width <= 3000 and image.height <= 3000


def test_object_key_is_content_addressed_and_example_scoped():
    digest = hashlib.sha256(b"photo").hexdigest()
    assert object_key("example", digest) == f"validation-photos/example/{digest}.jpg"


def test_normalize_image_rejects_non_image():
    try:
        normalize_image(b"not an image")
    except Exception:
        pass
    else:
        raise AssertionError("Non-image bytes were accepted")


def test_storage_upload_reuses_verified_content_addressed_object():
    blob = b"verified image bytes"
    calls = []
    def respond(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200,content=blob)
        raise AssertionError("Existing object should not be uploaded again")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert storage_upload(client,"a"*20,"validation-photos/e/d.jpg",blob) is False
    assert calls == ["GET"]


def test_storage_upload_handles_supabase_not_found_response():
    blob = b"new image bytes"
    stored = False
    def respond(request):
        nonlocal stored
        if request.method == "GET" and not stored:
            return httpx.Response(400,json={"error":"not_found","statusCode":"404"})
        if request.method == "POST":
            stored = True
            return httpx.Response(200,json={"Key":"saved"})
        if request.method == "GET" and stored:
            return httpx.Response(200,content=blob)
        raise AssertionError(request.method)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert storage_upload(client,"a"*20,"validation-photos/e/new.jpg",blob) is True


def test_storage_listing_scopes_returned_object_names():
    def respond(request):
        return httpx.Response(200,json=[{"name":"a.jpg"},{"name":"folder"}])
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert storage_objects(client,"a"*20,"validation-photos/example")==[
            "validation-photos/example/a.jpg"
        ]
