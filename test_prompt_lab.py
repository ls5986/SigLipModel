"""Offline PromptLab tests. All transient state stays under this project's folder."""

import ast
import base64
import copy
import hashlib
import io
import json
import shutil
import socket
import threading
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

import prompt_lab as module
from prompt_lab import FEATURES, KEY_ROOMS, PromptLab

SECRET = "sk-test-DO-NOT-PERSIST-123456789"


@pytest.fixture(autouse=True)
def deny_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real network access is forbidden in PromptLab tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(module.urllib.request.OpenerDirector, "open", forbidden)


@pytest.fixture
def factory():
    folder = Path(__file__).resolve().parent / (".prompt-lab-tests-" + uuid4().hex)
    folder.mkdir()
    managers = []

    def build(property_count=1, rooms=("kitchen", "bathroom", "living"), apply=None, key=None):
        root = folder / uuid4().hex
        sources = root / "source-photos"
        sources.mkdir(parents=True)
        paths, properties, applied, key_reads = {}, {}, [], []
        for number in range(property_count):
            prop_id = f"property-{number + 1}"
            rows = []
            for position, room in enumerate(rooms):
                image_id = f"source-{number + 1}-{position + 1}"
                path = sources / (image_id + ".png")
                Image.new("RGB", (16, 16), (number * 20, position * 30, 100)).save(path)
                paths[image_id] = path
                rows.append({
                    "id": image_id, "room": room, "room_source": "siglip",
                    "features": {"old_flooring": True},
                    "review": {"target_label": "DO_NOT_SEND_TARGET"},
                })
            properties[prop_id] = {
                "property": {"id": prop_id, "address": "DO_NOT_SEND_ADDRESS",
                             "close_price": "DO_NOT_SEND_PRICE", "agent": "DO_NOT_SEND_AGENT",
                             "condition_label": "DO_NOT_SEND_HUMAN_LABEL"},
                "images": rows, "property_suggestions": [{"secret": "DO_NOT_SEND_SUGGESTIONS"}],
            }

        def key_loader():
            key_reads.append(True)
            return key() if key else SECRET

        def applier(proposals):
            applied.append(copy.deepcopy(proposals))
            return apply(proposals) if apply else {"untrusted_return": SECRET}

        lab = PromptLab(root, properties.__getitem__, paths.__getitem__, applier, key_loader)
        managers.append(lab)
        return SimpleNamespace(root=root, lab=lab, paths=paths, properties=properties,
                               applied=applied, key_reads=key_reads, applier=applier,
                               key_loader=key_loader, managers=managers)

    yield build
    for manager in managers:
        if manager.thread:
            manager.thread.join(timeout=10)
            assert not manager.thread.is_alive(), "Offline worker did not finish"
    shutil.rmtree(folder)


def preview(env, **overrides):
    payload = {"property_ids": list(env.properties),
               "candidate_text": module.ROOM_FIRST_TEXT, **overrides}
    return env.lab.preview(payload)


def finish(env, identifier):
    env.lab.start({"id": identifier, "approved": True})
    env.lab.thread.join(timeout=10)
    assert not env.lab.thread.is_alive()
    return env.lab.result(identifier)


def output_for(request, *, rooms=None, label="UNKNOWN", consistency="unknown",
               remaining="unknown"):
    ids = [part["text"].split('"')[1] for part in request["input"][1]["content"]
           if part["type"] == "input_text"]
    rooms = rooms or list(KEY_ROOMS) + ["bedroom", "exterior", "outdoor", "other", "unknown"]
    images = [{
        "image_id": identifier, "room": rooms[index],
        "features": {feature: None for feature in FEATURES},
        "observations": ["Visible room finishes."],
    } for index, identifier in enumerate(ids)]
    coverage = {room: any(image["room"] == room for image in images) for room in KEY_ROOMS}
    return {
        "condition_label": label, "update_consistency": consistency,
        "remaining_work": remaining, "summary": "Visual condition remains uncertain.",
        "coverage": coverage, "images": images,
        "limitations": [] if all(coverage.values()) else [
            "Key-room coverage is incomplete; unseen areas remain unknown.",
        ],
    }


def response_for(structured_output, **overrides):
    return {
        "status": "completed", "model": "gpt-4.1-mini-2025-04-14",
        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(structured_output)},
        ]}], **overrides,
    }


def mock_model(env, transform=None, **output_options):
    calls = []

    def call(request):
        calls.append(copy.deepcopy(request))
        output = output_for(request, **output_options)
        if transform:
            transform(output)
        return response_for(output)

    env.lab._call_model = call
    return calls


def assert_no_secrets(env):
    for path in env.lab.root.rglob("*"):
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes(), path.name


def test_registry_is_explicit_immutable_and_returns_prompt_text(factory):
    env = factory()
    builtins = env.lab.list_prompts()
    assert [p["id"] for p in builtins] == ["legacy-any-update-v1", "room-first-v1"]
    assert "ANY visible photo" in builtins[0]["text"]
    assert all(p["hash"] == hashlib.sha256(p["text"].encode()).hexdigest() for p in builtins)
    saved = env.lab.save_prompt({"name": "A cautious candidate", "text": "Only visible evidence."})
    assert env.lab.save_prompt({"name": saved["name"], "text": saved["text"]}) == saved
    builtins[0]["text"] = "caller mutation"
    assert env.lab.list_prompts()[0]["text"] == module.BASELINE_TEXT
    with pytest.raises(ValueError):
        env.lab.save_prompt({"id": "legacy-any-update-v1", "name": "overwrite", "text": "no"})
    assert not env.key_reads


def test_feature_contract_matches_pilot_without_importing_its_runtime():
    tree = ast.parse(Path(__file__).with_name("pilot.py").read_text(encoding="utf-8"))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "FEATURES"
                              for target in node.targets))
    assert FEATURES == ast.literal_eval(assignment.value)
    schema = module.RESPONSE_SCHEMA["properties"]["images"]["items"]["properties"]["features"]
    assert set(schema["properties"]) == set(FEATURES)
    assert all(value == {"type": ["boolean", "null"]} for value in schema["properties"].values())


def test_seed_candidate_is_editable_as_a_frozen_new_comparison(factory):
    env = factory()
    seed = next(p for p in env.lab.list_prompts() if p["id"] == "room-first-v1")
    edited = seed["text"] + "\nPreserve updated countertop evidence in each room observation."
    saved = env.lab.save_prompt({"name": "Human-edited room-first candidate", "text": edited})
    frozen = preview(env, candidate_text=saved["text"])
    assert saved["id"] != seed["id"] and saved["hash"] != seed["hash"]
    assert frozen["candidate_hash"] == saved["hash"]
    assert env.lab.result(frozen["id"])["manifest"]["prompts"]["candidate"]["text"] == edited
    assert next(p for p in env.lab.list_prompts() if p["id"] == seed["id"])["text"] == seed["text"]
    assert not env.key_reads and not env.applied
    with pytest.raises(ValueError, match="approved=true"):
        env.lab.start({"id": frozen["id"], "approved": False})


@pytest.mark.parametrize("override", [
    {"property_ids": []}, {"property_ids": ["p"] * 2}, {"property_ids": [1]},
    {"property_ids": [""]}, {"property_ids": [" "]},
    {"property_ids": [str(i) for i in range(6)]}, {"property_ids": "property-1"},
    {"candidate_text": ""}, {"candidate_text": " "}, {"candidate_text": "a" * 20_001},
    {"images_per_property": 0}, {"images_per_property": 9},
    {"images_per_property": True}, {"images_per_property": 1.0},
    {"model": "https://untrusted.example"}, {"model": "model\nheader"}, {"model": ""},
    {"baseline_id": "unknown"}, {"baseline_id": []}, {"unexpected": True},
])
def test_preview_rejects_bad_bounds_without_credentials_or_requests(factory, override):
    env = factory()
    calls = mock_model(env)
    with pytest.raises(ValueError):
        preview(env, **override)
    assert not calls and not env.key_reads and not env.lab.list_runs()


def test_preview_requires_property_ids_and_candidate_and_counts_explicit_budget(factory):
    env = factory(property_count=5)
    with pytest.raises(ValueError):
        env.lab.preview({"candidate_text": "candidate"})
    with pytest.raises(ValueError):
        env.lab.preview({"property_ids": ["property-1"]})
    three = preview(env, property_ids=list(env.properties)[:3], images_per_property=2)
    five = preview(env, images_per_property=1, model="gpt-4.1")
    assert (three["property_count"], three["image_count"], three["planned_calls"]) == (3, 6, 6)
    assert five["planned_calls"] == five["max_calls"] == 10
    assert five["estimated_cost"] is None
    assert "unknown" in five["pricing_status"]
    assert all(1 <= len(p["mapping"]) <= 8 for p in five["selected_images"])
    assert not env.key_reads and not env.applied


@pytest.mark.parametrize("approval", [False, None, "true", 1, 0])
def test_start_requires_literal_explicit_approval(factory, approval):
    env = factory()
    calls = mock_model(env)
    frozen = preview(env)
    with pytest.raises(ValueError):
        env.lab.start({"id": frozen["id"], "approved": approval})
    with pytest.raises(ValueError):
        env.lab.start({"id": frozen["id"]})
    assert env.lab.result(frozen["id"])["status"] == "preview"
    assert not calls and not env.key_reads


def test_room_coverage_human_preference_hash_dedup_and_missing_images(factory):
    env = factory(rooms=("kitchen", "bathroom", "living", "kitchen", "bathroom", "exterior"))
    rows = env.properties["property-1"]["images"]
    rows[3].update(room="other", review={"room": "kitchen", "status": "approved"})
    env.paths[rows[4]["id"]].write_bytes(env.paths[rows[1]["id"]].read_bytes())
    env.paths[rows[5]["id"]].unlink()
    frozen = preview(env)
    selected = frozen["selected_images"][0]
    assert [p["room"] for p in selected["mapping"][:3]] == list(KEY_ROOMS)
    assert selected["image_ids"][0] == rows[3]["id"]
    assert selected["mapping"][0]["selection_source"] == "human_preferred"
    assert selected["skipped_images"]["duplicate_bytes"] == 1
    assert selected["skipped_images"]["missing_image"] == 1
    hashes = [p["sha256"] for p in selected["mapping"]]
    assert len(hashes) == len(set(hashes)) == 4
    assert [p["index"] for p in selected["mapping"]] == [1, 2, 3, 4]


def test_studio_human_approved_room_source_is_preferred(factory):
    env = factory(rooms=("kitchen", "kitchen"))
    rows = env.properties["property-1"]["images"]
    rows[1].update(room_source="Human approved", review={})
    frozen = preview(env, images_per_property=1)
    selected = frozen["selected_images"][0]["mapping"][0]
    assert selected["source_image_id"] == rows[1]["id"]
    assert selected["selection_source"] == "human_preferred"


def test_frozen_original_bytes_same_opaque_mapping_both_arms_no_metadata_leak(factory):
    env = factory()
    original = {identifier: path.read_bytes() for identifier, path in env.paths.items()}
    frozen = preview(env)
    calls = mock_model(env)
    for path in env.paths.values():
        path.write_bytes(b"source changed after preview")
    result = finish(env, frozen["id"])
    assert result["status"] == "completed"
    assert len(calls) == result["calls_attempted"] == 2
    assert calls[0]["input"][1] == calls[1]["input"][1]
    mapping = frozen["selected_images"][0]["mapping"]
    image_parts = [p for p in calls[0]["input"][1]["content"] if p["type"] == "input_image"]
    for photo, part in zip(mapping, image_parts, strict=True):
        sent = base64.b64decode(part["image_url"].split(",", 1)[1])
        assert sent == original[photo["source_image_id"]]
        assert hashlib.sha256(sent).hexdigest() == photo["sha256"]
    serialized = json.dumps(calls)
    for forbidden in ("DO_NOT_SEND", "property-1", "source-1-", SECRET, "old_flooring\": true"):
        assert forbidden not in serialized
    assert all(request["store"] is False for request in calls)
    assert calls[0]["text"] == calls[1]["text"]
    assert calls[0]["input"][0] != calls[1]["input"][0]
    assert result["usage"] == {
        "input_tokens": 200, "output_tokens": 100, "total_tokens": 300, "complete": True,
    }
    assert result["actual_cost"] is None
    assert "not the exact production validator" in result["comparison_note"]
    assert "Diagnostic prompt-only baseline" in result["comparison_note"]
    assert not env.applied and not env.key_reads


def test_preview_frozen_photos_and_manifest_cannot_change_before_start(factory):
    env = factory()
    calls = mock_model(env)
    frozen = preview(env)
    manifest = env.lab.result(frozen["id"])["manifest"]
    photo = manifest["properties"][0]["images"][0]
    (env.lab.runs / frozen["id"] / photo["file"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash/size"):
        env.lab.start({"id": frozen["id"], "approved": True})
    other = preview(env)
    path = env.lab.runs / other["id"] / "manifest.json"
    contents = json.loads(path.read_text())
    contents["version"] = 999
    path.write_text(json.dumps(contents))
    with pytest.raises(ValueError, match="manifest/version"):
        env.lab.start({"id": other["id"], "approved": True})
    assert not calls and not env.key_reads


def test_no_usable_photos_skips_both_calls_and_never_reads_key(factory):
    env = factory(rooms=())
    calls = mock_model(env)
    frozen = preview(env)
    assert frozen["image_count"] == 0 and "no usable photos" in " ".join(frozen["warnings"])
    result = finish(env, frozen["id"])
    assert result["status"] == "failed"
    assert result["calls_attempted"] == 0 and result["planned_calls"] == 2
    assert all(result["properties"][0][arm]["status"] == "skipped_no_usable_photos"
               for arm in module.ARMS)
    assert not calls and not env.key_reads
    with pytest.raises(ValueError):
        env.lab.apply({"id": frozen["id"], "arm": "candidate"})


def test_bad_or_oversized_sources_are_explicitly_excluded(factory):
    env = factory(rooms=("kitchen", "bathroom"))
    env.paths["source-1-1"].write_bytes(b"not an image")
    env.paths["source-1-2"].write_bytes(b"x" * (module.MAX_IMAGE_BYTES + 1))
    frozen = preview(env)
    assert frozen["image_count"] == 0
    skipped = frozen["selected_images"][0]["skipped_images"]
    assert skipped["invalid_image"] == 1 and skipped["image_too_large"] == 1


def test_truncated_jpeg_and_network_image_paths_are_not_usable(factory):
    env = factory(rooms=("kitchen", "bathroom"))
    path = env.paths["source-1-1"]
    Image.new("RGB", (16, 16), "blue").save(path, format="JPEG")
    path.write_bytes(path.read_bytes()[:-10])
    env.paths["source-1-2"] = Path(r"\\untrusted-server\share\photo.jpg")
    frozen = preview(env)
    assert frozen["image_count"] == 0
    assert frozen["selected_images"][0]["skipped_images"]["nonlocal_image"] == 1
    assert not env.key_reads


def test_total_selected_byte_limit_rejects_without_creating_run(factory, monkeypatch):
    env = factory()
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 1)
    with pytest.raises(ValueError, match="total limit"):
        preview(env)
    assert env.lab.list_runs() == [] and not env.key_reads


def test_baseline_partial_updates_accepted_but_candidate_rejected_without_rewrite(factory):
    env = factory(rooms=("kitchen",))
    calls = mock_model(env, label="UPDATED_TURNKEY", consistency="mixed", remaining="cosmetic",
                       transform=lambda out: out["images"][0]["features"].update(old_cabinetry=True))
    frozen = preview(env)
    result = finish(env, frozen["id"])
    baseline, candidate = (result["properties"][0][arm] for arm in module.ARMS)
    assert baseline["status"] == "succeeded"
    assert baseline["validation"]["policy"] == "common-schema-only"
    assert candidate["status"] == "validation_failed"
    assert candidate["raw_output"]["condition_label"] == "UPDATED_TURNKEY"
    assert any("requires kitchen" in error for error in candidate["validation"]["errors"])
    assert result["status"] == "completed_with_errors" and len(calls) == 2
    assert not env.applied
    with pytest.raises(ValueError):
        env.lab.apply({"id": frozen["id"], "arm": "candidate"})
    assert env.lab.apply({"id": frozen["id"], "arm": "baseline"})["proposal_count"] == 1


@pytest.mark.parametrize("label,accepted", [("UNKNOWN", True), ("DATED_VALUE_ADD", False)])
def test_no_keyroom_evidence_requires_unknown_candidate(factory, label, accepted):
    env = factory(rooms=("exterior",))
    mock_model(env, rooms=["exterior"], label=label)
    frozen = preview(env)
    result = finish(env, frozen["id"])
    candidate = result["properties"][0]["candidate"]
    assert candidate["validation"]["accepted"] is accepted
    assert candidate["raw_output"]["condition_label"] == label
    assert all(value is None for value in candidate["raw_output"]["images"][0]["features"].values())


@pytest.mark.parametrize("mutate", [
    lambda out: out["images"][0].update(image_id="unknown-id"),
    lambda out: out["images"].append(copy.deepcopy(out["images"][0])),
    lambda out: out["images"].pop(),
    lambda out: out["coverage"].update(kitchen=False),
    lambda out: out.update(condition_label="TURNKEY"),
    lambda out: out["images"][0]["features"].update(old_cabinetry=1),
    lambda out: out["images"][0]["features"].update(old_cabinetry=float("nan")),
    lambda out: out["images"][0]["features"].update(old_cabinetry=float("inf")),
    lambda out: out["images"][0]["features"].update(old_cabinetry="UNKNOWN"),
    lambda out: out["images"][0]["features"].update(older_cabinets=True),
    lambda out: out["images"][0]["features"].update(updated_finishes=True),
    lambda out: out["images"][0]["features"].update(visible_major_damage=True),
    lambda out: out.update(unexpected="key"),
    lambda out: out.update(summary="Evidence in img_not_sent."),
    lambda out: out.update(summary="See photo 99."),
    lambda out: out.update(summary="Profit should be high."),
    lambda out: out.update(summary="Photos were taken recently."),
])
def test_common_schema_and_citation_errors_fail_both_arms(factory, mutate):
    env = factory()
    mock_model(env, transform=mutate)
    frozen = preview(env)
    result = finish(env, frozen["id"])
    assert result["status"] == "failed"
    for arm in module.ARMS:
        outcome = result["properties"][0][arm]
        assert outcome["status"] == "validation_failed"
        assert not outcome["validation"]["accepted"] and outcome["validation"]["errors"]
    assert not env.applied


@pytest.mark.parametrize("feature", ["old_cabinetry", "damaged_surfaces",
                                    "clutter_obscures_condition", "visible_deferred_maintenance"])
def test_candidate_turnkey_requires_no_visible_contradiction(factory, feature):
    env = factory()
    mock_model(env, label="UPDATED_TURNKEY", consistency="consistent", remaining="none_visible",
               transform=lambda out: out["images"][0]["features"].update({feature: True}))
    frozen = preview(env)
    result = finish(env, frozen["id"])
    assert result["properties"][0]["baseline"]["status"] == "succeeded"
    assert result["properties"][0]["candidate"]["status"] == "validation_failed"


def test_fully_covered_consistent_turnkey_is_accepted(factory):
    env = factory()
    mock_model(env, label="UPDATED_TURNKEY", consistency="consistent", remaining="none_visible")
    frozen = preview(env)
    assert finish(env, frozen["id"])["status"] == "completed"


@pytest.mark.parametrize("label,remaining,major,accepted", [
    ("ROUGH_HEAVY_VALUE_ADD", "cosmetic", False, False),
    ("ROUGH_HEAVY_VALUE_ADD", "substantial", False, False),
    ("ROUGH_HEAVY_VALUE_ADD", "substantial", True, True),
    ("MAJOR_REHAB", "major", False, False),
    ("MAJOR_REHAB", "major", True, True),
])
def test_candidate_severity_requires_visible_support(factory, label, remaining, major, accepted):
    env = factory()

    def evidence(output):
        image = output["images"][0]
        image["features"]["damaged_surfaces"] = major
        if major:
            image["observations"] = ["Major visible damage to walls."]

    mock_model(env, label=label, remaining=remaining, transform=evidence)
    frozen = preview(env)
    result = finish(env, frozen["id"])
    assert result["properties"][0]["candidate"]["validation"]["accepted"] is accepted


@pytest.mark.parametrize("observation,accepted", [
    ("Minor scuffs on the painted wall.", False),
    ("No major visible damage.", False),
    ("The ceiling has collapsed.", True),
    ("Extensive visible damage to the floor.", True),
])
def test_major_damage_requires_room_cited_observations_not_just_surface_tag(factory, observation, accepted):
    env = factory()

    def evidence(output):
        image = output["images"][0]
        image["features"]["damaged_surfaces"] = True
        image["observations"] = [observation]

    mock_model(env, label="MAJOR_REHAB", remaining="major", transform=evidence)
    frozen = preview(env)
    result = finish(env, frozen["id"])
    assert result["properties"][0]["candidate"]["validation"]["accepted"] is accepted


@pytest.mark.parametrize("prose,accepted", [
    ("Older cabinets remain in the kitchen.", False),
    ("Repairs are needed in the bathroom.", False),
    ("No repairs needed.", True),
    ("No damaged surfaces are visible.", True),
    ("No major damage, but older cabinets remain.", False),
])
def test_turnkey_rejects_clear_prose_contradictions_without_rewriting(factory, prose, accepted):
    env = factory()
    mock_model(env, label="UPDATED_TURNKEY", consistency="consistent", remaining="none_visible",
               transform=lambda out: out.update(summary=prose))
    frozen = preview(env)
    result = finish(env, frozen["id"])
    candidate = result["properties"][0]["candidate"]
    assert candidate["validation"]["accepted"] is accepted
    assert candidate["raw_output"]["condition_label"] == "UPDATED_TURNKEY"
    assert result["properties"][0]["baseline"]["status"] == "succeeded"


@pytest.mark.parametrize("text", [
    '{"condition_label": "UNKNOWN", "number": 1e999}',
    '{"condition_label": "UNKNOWN", "condition_label": "UPDATED_TURNKEY"}',
    "not JSON", "null", "[]",
])
def test_invalid_json_nonfinite_exponents_and_duplicate_keys_have_validation_outcomes(factory, text):
    env = factory()
    env.lab._call_model = lambda request: response_for({}, output=[
        {"type": "message", "content": [{"type": "output_text", "text": text}]},
    ])
    frozen = preview(env)
    result = finish(env, frozen["id"])
    assert result["status"] == "failed"
    assert all(result["properties"][0][arm]["status"] == "validation_failed" for arm in module.ARMS)


def test_start_is_idempotent_single_worker_and_small_polling_summaries(factory):
    env = factory()
    one, two = preview(env), preview(env)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked(request):
        calls.append(request)
        entered.set()
        assert release.wait(timeout=5)
        return response_for(output_for(request))

    env.lab._call_model = blocked
    try:
        env.lab.start({"id": one["id"], "approved": True})
        assert entered.wait(timeout=5)
        same = env.lab.start({"id": one["id"], "approved": True})
        assert same["status"] == "running" and same["calls_attempted"] == 1
        with pytest.raises(RuntimeError, match="already running"):
            env.lab.start({"id": two["id"], "approved": True})
        with pytest.raises(ValueError):
            env.lab.apply({"id": one["id"], "arm": "baseline"})
        assert all("properties" not in row and "manifest" not in row for row in env.lab.list_runs())
    finally:
        release.set()
    env.lab.thread.join(timeout=10)
    finished = env.lab.start({"id": one["id"], "approved": True})
    assert finished["status"] == "completed" and len(calls) == 2


def test_http_transport_is_single_attempt_timeout_store_false_and_never_persists_key(factory, monkeypatch):
    env = factory()
    attempted = []

    class Opener:
        def open(self, request, timeout):
            attempted.append((request, timeout))
            raise urllib.error.HTTPError(request.full_url, 429, SECRET,
                                         {"Authorization": SECRET}, io.BytesIO(SECRET.encode()))

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *args: Opener())
    frozen = preview(env)
    assert not env.key_reads
    result = finish(env, frozen["id"])
    assert len(attempted) == len(env.key_reads) == result["calls_attempted"] == 2
    for request, timeout in attempted:
        assert request.full_url == "https://api.openai.com/v1/responses"
        assert request.get_header("Authorization") == "Bearer " + SECRET
        assert timeout == 90 and json.loads(request.data)["store"] is False
        assert SECRET.encode() not in request.data
    for arm in module.ARMS:
        assert result["properties"][0][arm]["error"] == {"code": "http_error", "http_status": 429}
    assert_no_secrets(env)
    assert module._NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere") is None


@pytest.mark.parametrize("kind", ["timeout", "transport", "unexpected", "refusal", "incomplete"])
def test_safe_error_outcomes_no_retries_or_autoapply(factory, kind):
    env = factory()
    calls = []

    def error(request):
        calls.append(True)
        if kind == "timeout":
            raise module._RequestError("timeout")
        if kind == "transport":
            raise module._RequestError("transport_error")
        if kind == "unexpected":
            raise RuntimeError(SECRET)
        if kind == "refusal":
            return response_for({}, output=[{"type": "message", "content": [
                {"type": "refusal", "refusal": SECRET},
            ]}])
        return response_for(output_for(request), status="incomplete")

    env.lab._call_model = error
    frozen = preview(env)
    result = finish(env, frozen["id"])
    assert result["status"] == "failed" and len(calls) == result["calls_attempted"] == 2
    assert all(result["properties"][0][arm]["status"] == "failed" for arm in module.ARMS)
    expected = {"timeout": "timeout", "transport": "transport_error",
                "unexpected": "request_failed", "refusal": "model_refusal",
                "incomplete": "incomplete_or_failed_response"}[kind]
    assert all(result["properties"][0][arm]["error"]["code"] == expected for arm in module.ARMS)
    assert not env.applied
    assert_no_secrets(env)


def test_credential_loader_failure_is_sanitized_and_is_only_read_at_execution(factory):
    def key():
        raise RuntimeError(SECRET)

    env = factory(key=key)
    frozen = preview(env)
    assert not env.key_reads
    result = finish(env, frozen["id"])
    assert env.key_reads == [True, True]
    assert result["properties"][0]["candidate"]["error"]["code"] == "credentials_unavailable"
    assert_no_secrets(env)


def test_restart_marks_running_interrupted_without_silent_resume(factory):
    env = factory()
    frozen = preview(env)
    path = env.lab.runs / frozen["id"] / "state.json"
    state = json.loads(path.read_text())
    state["status"] = "running"
    state["properties"][0]["baseline"]["status"] = "running"
    path.write_text(json.dumps(state))
    restarted = PromptLab(env.root, env.properties.__getitem__, env.paths.__getitem__,
                          env.applier, env.key_loader)
    env.managers.append(restarted)
    result = restarted.result(frozen["id"])
    assert result["status"] == "interrupted"
    assert all(result["properties"][0][arm]["status"] == "interrupted" for arm in module.ARMS)
    assert restarted.start({"id": frozen["id"], "approved": True})["status"] == "interrupted"
    assert restarted.thread is None and not env.key_reads


def test_apply_maps_only_successful_drafts_and_is_durable_idempotent(factory):
    env = factory()
    mock_model(env)
    frozen = preview(env)
    finish(env, frozen["id"])
    assert not env.applied
    receipt = env.lab.apply({"id": frozen["id"], "arm": "candidate"})
    assert receipt["status"] == "applied" and receipt["proposal_count"] == 1
    assert env.lab.apply({"id": frozen["id"], "arm": "candidate"}) == receipt
    assert len(env.applied) == 1
    proposal = env.applied[0][0]
    assert proposal["source"] == "llm" and proposal["run_id"] == frozen["id"]
    assert proposal["prompt_hash"] == frozen["candidate_hash"]
    assert proposal["proposal_id"] == proposal["idempotency_key"]
    assert [image["image_id"] for image in proposal["images"]] == list(env.paths)
    assert all(image["citation_image_id"].startswith("img_") for image in proposal["images"])
    assert "reviewed" not in proposal and "review" not in proposal
    assert proposal["property"]["condition_label"] == "UNKNOWN"
    restarted = PromptLab(env.root, env.properties.__getitem__, env.paths.__getitem__,
                          env.applier, env.key_loader)
    env.managers.append(restarted)
    assert restarted.apply({"id": frozen["id"], "arm": "candidate"}) == receipt
    assert len(env.applied) == 1
    assert_no_secrets(env)


def test_updated_evidence_and_unknown_null_features_survive_draft_application(factory):
    env = factory()
    observations = [
        "The kitchen has updated countertops and visible older cabinetry.",
        "The bathroom has updated tile around the shower.",
        "The living room has updated flooring.",
    ]

    def evidence(output):
        for image, observation in zip(output["images"], observations, strict=True):
            image["observations"] = [observation]
        output["images"][0]["features"]["old_cabinetry"] = True

    mock_model(env, label="DATED_VALUE_ADD", consistency="mixed", remaining="cosmetic",
               transform=evidence)
    frozen = preview(env)
    result = finish(env, frozen["id"])
    assert result["status"] == "completed"
    env.lab.apply({"id": frozen["id"], "arm": "candidate"})
    bundle = env.applied[0][0]
    assert {"property_id", "run_id", "prompt_hash", "model", "source", "images", "property"} <= bundle.keys()
    assert bundle["source"] == "llm"
    for index, image in enumerate(bundle["images"]):
        assert image["observations"] == [observations[index]]
        assert set(image["features"]) == set(FEATURES)
        for feature, value in image["features"].items():
            assert value is (True if index == 0 and feature == "old_cabinetry" else None)
    assert bundle["property"]["condition_label"] == "DATED_VALUE_ADD"


def test_arbitrary_api_error_text_is_not_saved_or_exposed(factory):
    env = factory()
    private_error = "API-private-detail-" * 20_000 + SECRET
    env.lab._call_model = lambda request: response_for(
        {}, status="failed", error={"message": private_error},
    )
    frozen = preview(env)
    result = finish(env, frozen["id"])
    for arm in module.ARMS:
        outcome = result["properties"][0][arm]
        assert outcome["error"] == {"code": "incomplete_or_failed_response"}
    assert "API-private-detail-" not in json.dumps(result)
    assert_no_secrets(env)


def test_apply_callback_errors_are_safe_and_explicit_retry_reuses_proposal_keys(factory):
    calls = []

    def apply(proposals):
        calls.append(copy.deepcopy(proposals))
        if len(calls) == 1:
            raise RuntimeError(SECRET)

    env = factory(apply=apply)
    mock_model(env)
    frozen = preview(env)
    finish(env, frozen["id"])
    with pytest.raises(RuntimeError, match="Draft callback failed") as error:
        env.lab.apply({"id": frozen["id"], "arm": "candidate"})
    assert SECRET not in str(error.value)
    receipt = env.lab.apply({"id": frozen["id"], "arm": "candidate"})
    assert receipt["status"] == "applied" and calls[0] == calls[1]
    assert_no_secrets(env)


def test_apply_partial_run_only_includes_selected_arm_successes(factory):
    env = factory(property_count=2)
    calls = []

    def call(request):
        calls.append(request)
        output = output_for(request)
        if len(calls) == 4:
            output["images"][0]["image_id"] = "bad-citation"
        return response_for(output)

    env.lab._call_model = call
    frozen = preview(env)
    result = finish(env, frozen["id"])
    assert result["status"] == "completed_with_errors"
    receipt = env.lab.apply({"id": frozen["id"], "arm": "candidate"})
    assert receipt["proposal_count"] == 1
    assert [p["property_id"] for p in env.applied[0]] == ["property-1"]


def test_bounded_run_count_and_safe_run_identifiers(factory, monkeypatch):
    env = factory()
    monkeypatch.setattr(module, "MAX_RUNS", 2)
    preview(env)
    preview(env)
    with pytest.raises(ValueError, match="run limit"):
        preview(env)
    assert len(env.lab.list_runs()) == 2
    for bad in ("..\\elsewhere", "../elsewhere", "", None):
        with pytest.raises(ValueError):
            env.lab.result(bad)
    assert not list(env.lab.root.rglob("*.writing"))


def test_strict_schema_objects_require_all_keys_and_disallow_extras():
    def verify(schema):
        if schema["type"] == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
            for value in schema["properties"].values():
                verify(value)
        elif schema["type"] == "array":
            verify(schema["items"])

    verify(module.RESPONSE_SCHEMA)
