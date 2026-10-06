# ActVision v2 contract

## Versioned bundle

[`manifest.json`](manifest.json) is the authoritative, deterministic bundle
index. [`VERSION`](VERSION) contains its independent Semantic Versioning
identity; the initial bundle is `1.0.0`. The bundle version does not replace the
`actvision-v2` wire version or `actvision-labels-v2` taxonomy version. The
manifest records the provider, owner, source commit, compatibility policy,
fixture generator command, and raw-byte SHA-256 for the schema, semantic
validator, generator, and every synthetic fixture. Paths are sorted,
repository-relative POSIX paths.

Validate the complete local bundle without network access:

```powershell
python tools/check_actvision_contract_bundle.py
python tools/export_actvision_fixtures.py --check
```

Consumers must pin an independently approved exact `manifest.json` and reject
missing, additional, duplicated, reordered, or byte-mismatched bundle files.
`source_commit` is provenance for the bundled bytes; it is not the later commit
that adds the manifest.

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
  Percent-encode the complete release ID as one suffix; the producer decodes it
  once, preserving valid spaces, slashes, Unicode and literal percent escapes.
* Both require `Authorization: Bearer <ACTVISION_SERVICE_TOKEN>` (32+ characters),
  configured independently from browser login. No cookie or review token needed.
  HTTPS is mandatory outside local test. Existing host restrictions remain.
* Missing/invalid bearer: 401; bad schema/identity: 400; conflict: 409;
  unavailable database/model/service: 503. Retry boundedly on 503, not on 400.
  Connection failures and missing migration tables/columns return sanitized
  JSON 503 responses, with error-class-only server logs. Studio v2 cloud routes
  likewise report `studio_unavailable` and instruct the operator to verify
  database connectivity and required migrations; no SQL or credentials are sent.

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

## Compatibility policy

Bundle versions follow Semantic Versioning:

- **PATCH** records compatible corrections that preserve every valid payload and
  existing meaning.
- **MINOR** may add optional fields or enum behavior only when existing payloads,
  validation, and semantics remain compatible.
- **MAJOR** is required for changes to required fields, canonicalization, label
  meaning, evidence or release identity, status meaning, or the validity of
  existing payloads. A breaking release requires a separately approved migration
  specification and explicit consumer migration.

The provider publishes and validates a new bundle before consumer enablement.
The consumer then pins the exact approved manifest and verifies it offline
before shadow use. Rollback disables consumer behavior first, restores the
previous approved manifest and compatible provider release, and preserves
historical requests, predictions, feedback, and releases. See
[`CHANGELOG.md`](CHANGELOG.md) for bundle history.
