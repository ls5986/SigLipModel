# ActVision v2 contract

`actvision-v2.schema.json` is authoritative for both repositories. Its `$defs`
contain `inference_request`, `prediction`, `feedback`, and `release`. Validate the
JSON Schema **and** the cross-field rules in `actvision_contract.py`. Fixtures
are synthetic, not model outputs. Regenerate with
`python tools\export_actvision_fixtures.py`; check with `--check`.

## Identity and unknown semantics

Wire version: `actvision-v2`. Taxonomy: `actvision-labels-v2`. Legacy v1 artifacts,
labels and APIs remain unchanged. Condition, modernization and acquisition fit
are independent; target similarity is NOT condition, confidence or deal quality.
Economics are owned by MLS Sourcing, not the physical result.

`evidence_id` is SHA256 of evidence JSON encoded UTF8 with keys recursively sorted,
ASCII escaping, no whitespace (`separators=(",", ":")`), no NaN/Infinity.
Array order is significant. Hash remarks from their exact UTF8 bytes, without
trimming. Hash structured fields using the canonical JSON algorithm, before
normalizing model features. Use identical numeric serialization on both sides.
Pin the source snapshot timestamp; do not generate a fresh timestamp on retries.

Identity includes workspace, property, listing, snapshot, visual generation,
photo-change timestamp, ordered selected photo IDs, URI hashes and any known
image hashes, remarks hash, structured hash, and release. A URI hash is **not**
an image-content hash. Use null for an unknown input image SHA. Production must
return real image-byte hashes in `observed_photo_hashes` for photos actually used.
Never relabel changed bytes as the original evidence. Changed photos/remarks/
structured fields/release produce a new identity and a new job.

Component status is `available`, `missing` (no input), `unavailable` (no compatible
trained artifact), or `failed`. Non-available components have UNKNOWN labels,
empty probability objects, null scores/confidence and an explicit reason.
Text signal state is PRESENT/ABSENT/UNKNOWN; omission is unassessed, not ABSENT.
Snippet spans use Unicode code-point offsets, start inclusive/end exclusive.
UNKNOWN never becomes a negative training label.

## HTTP transport

* `POST /api/actvision/v2/infer`: request -> prediction. Default off; 503
  `{"error": "...", "code": "actvision_unavailable"}` until approved physical
  evidence artifacts and runtime adapters are provisioned. No fabricated results.
* `POST /api/actvision/v2/feedback`: feedback -> 200
  `{"event_id": "...", "status": "accepted" | "duplicate"}`. Exact retries are
  idempotent; reused event ID with different content is 409. Events are append-only
  pending review, not training approval.
* `GET /api/actvision/v2/releases/{release_id}`: verified configured manifest,
  using the same bearer token. Missing/incompatible bundle returns 503; a
  different configured release ID returns 404. Consumers pin and compare this
  manifest to their independently approved local copy before accepting outputs.
* Both require `Authorization: Bearer <ACTVISION_SERVICE_TOKEN>` (32+ characters),
  configured independently from browser login. No cookie or review token needed.
  HTTPS is mandatory outside local test. Existing host restrictions remain.
* Missing/invalid bearer: 401; bad schema/identity: 400; conflict: 409;
  unavailable database/model/service: 503. Retry boundedly on 503, not on 400.

Feedback identifies the source prediction, full original evidence, release and
reviewer; it cannot replace historical predictions or mutate frozen datasets.
New corrections use new event IDs and optional `supersedes_event_id`.
Service credentials may import only into their configured workspace.

## Release gate

All four component slots are explicit; null means unavailable. Each non-null
artifact pins URI, SHA256, framework/version, feature/label/calibration versions.
Candidate bundles are not approved automatically. Shadow/production require
recorded operator approval and passing protected-slice evaluation. The loader
checks schema, approval, compatibility, path containment and bytes before any
adapter may deserialize. A hash alone is not permission to execute pickle code.
Only operator-provisioned trusted local bundles are supported in this rebuild.
