# Research: ActVision v2 Provider Foundation

## Purpose and Method

This research records the provider behavior that already exists, the behavior
that is experimental, and the decisions still required for a full multimodal
release. It is based on the checked-in provider README, contract documentation,
v2 model/runtime modules, experimental-candidate documentation, and the approved
Spec Kit provider-project design. It authorizes no runtime or external-system
change.

## Existing Provider Baseline

### Evidence and review

- Reviews distinguish physical condition, modernization, acquisition fit, and
  17 semantic text signals. UNKNOWN is an explicit value.
- Human labels retain revision and evidence information. Changed photos, remarks,
  facts, or exclusions invalidate the prior evidence fingerprint.
- Listing/source identity and photo era are separate review concerns. Known
  targets, imported rows, draft labels, and cohort membership are not automatic
  approved v2 labels.
- Physical properties and linked evidence are grouped for split isolation.
  Protected groups are excluded from fitting.

### Model components

- The repository preserves V0 positive-reference similarity and legacy v1
  artifacts. They are not v2 physical-condition releases.
- Vision experiments use frozen `google/siglip2-base-patch16-224` embeddings with
  property-level bag aggregation. Existing zero-shot room/context outputs are
  drafts, not human truth.
- The checked-in text baseline is supervised TF-IDF. A pinned frozen semantic
  encoder path exists and supports supervised contextual vectors, but provisioning
  an encoder is not training the task heads.
- Structured models use allowlisted acquisition-time facts and exclude arbitrary
  fields and later outcomes.
- Fusion code exists for component scores and availability, but grouped
  out-of-fold full-v2 orchestration, final calibration, protected acceptance, and
  compatible release adapters remain gates.

### Runtime and storage

- The hosted review service is intentionally lightweight; model-capable work runs
  in separate workers.
- Revisioned workspace state and private media can be held in Supabase. This
  feature does not apply migrations or modify that project.
- `POST /api/actvision/v2/infer` deliberately returns an unavailable response
  until approved physical artifacts and adapters exist.
- Feedback is append-only and pending review. Release transitions are explicit.
- The v2 schema and `actvision_contract.py` cross-field rules already define the
  provider wire semantics. Legacy v1 APIs and artifacts remain supported.

## Experimental Candidate Finding

The hosted experimental path trains provisional semantic-text and allowlisted
metadata heads from an explicitly selected mixture of drafts and human
corrections. It masks UNKNOWN, protects physical groups, and records dataset and
encoder identities. It does not use photos, train SigLIP2, calibrate
probabilities, fit multimodal fusion, or create an approved v2 release.

**Decision**: Keep this path labeled **experimental text/metadata candidate**.
Its predictions remain separate from labels and the release registry. No metric
from that path may be presented as evidence that the full multimodal provider is
approved.

## Adopted Foundation Decisions

### D-001 — Provider/product ownership

**Decision**: SigLipModel owns physical-evidence provider semantics and the
ActVision contract. MLSSourcing owns economics, opportunity policy, and ranking.

**Reason**: Separating observation from product decision prevents physical model
output from becoming a canonical investment judgment without consumer policy.

### D-002 — Authoritative compatibility source

**Decision**: `contracts/actvision-v2.schema.json` plus
`actvision_contract.py` are authoritative. A versioned manifest may package those
bytes and fixtures but may not replace their meaning.

**Reason**: Both schema shape and cross-field identity/unknown rules are necessary
for safe compatibility.

### D-003 — Independent optional components

**Decision**: Full-v2 releases represent vision, semantic text, structured
evidence, and fusion separately. Null or unavailable components stay explicit.

**Reason**: Missing modalities are normal in MLS evidence. Component identities
and honest availability are required to interpret a prediction.

### D-004 — Frozen representation backbones

**Decision**: The initial full-v2 plan keeps SigLIP2 and the semantic encoder
frozen and trains bounded supervised heads.

**Reason**: The current reviewed dataset is not sufficient to justify backbone
fine-tuning. Frozen backbones reduce artifact, compute, and reproducibility risk.
A future feature may propose fine-tuning with new evidence.

### D-005 — Grouped out-of-fold fusion

**Decision**: Fusion and calibration inputs are produced out-of-fold by physical
property group. Protected labels are not available to fitting or selection.

**Reason**: In-sample component outputs would overstate fusion performance and
photo-level splits would leak the same property across stages.

### D-006 — Human-per-axis release evidence

**Decision**: Draft agreement, imported cohort labels, and aggregate metrics are
insufficient for promotion. Numeric thresholds are pre-registered per supported
axis and protected slice before protected evaluation.

**Reason**: Current experimental labels and sparse classes cannot support honest
release claims.

### D-007 — Provider-first contract rollout

**Decision**: Compatible provider artifacts and offline fixture checks precede
consumer enablement. Consumers pin and validate an independently approved bundle.

**Reason**: A branch name, deploy identifier, or mutable URL is not a compatibility
or release identity.

## Open Decisions and Their Gates

These are deliberate future decisions, not permission to guess during
implementation.

| ID | Decision needed | Required evidence and owner | Behavior until resolved |
|---|---|---|---|
| OD-001 | Numeric per-axis and protected-slice acceptance thresholds | ML/release owner reviews class coverage, costs, calibration, and failure cases before protected evaluation | No shadow or production promotion |
| OD-002 | Final SigLIP2 property aggregation (max, top-k, mean+max, or learned attention) | ML owner compares candidates using grouped validation; protected test remains unread | No claim that current aggregation is approved |
| OD-003 | Calibration family and abstention thresholds for each head/mode | ML owner measures reliability and minimum group counts on validation | Confidence and unsupported probabilities remain null |
| OD-004 | Fusion inputs and missing-modality strategy | ML owner verifies grouped OOF component predictions and each modality slice | Experimental text/metadata stays separate from full v2 |
| OD-005 | Supported class set for the first release | Label/release owners confirm independent human-approved coverage for each class | Sparse classes stay unavailable/UNKNOWN; no forced relabeling |
| OD-006 | Retention periods for evidence, predictions, feedback, and worker diagnostics | Security/data owners approve policy consistent with source rights and recovery | Existing stricter retention and no-deletion safeguards remain |
| OD-007 | Hosted model-worker capacity, isolation, and rollout topology | Operations/security owners validate least privilege, budgets, recovery, and shadow throughput | No new service, deployment, migration, or production traffic |
| OD-008 | Consumer shadow acceptance and rollback window | SigLipModel and MLSSourcing owners validate pinned bundle and end-to-end mismatch handling | Consumer integration remains default-off shadow only |

## Rejected Approaches

- **Convert unknowns to negatives**: rejected because absence of review or
  modality is not contrary evidence.
- **Use later sale outcomes as acquisition features**: rejected as temporal
  leakage and a product-economics boundary violation.
- **Treat V0 similarity or legacy target probability as v2 condition**: rejected
  because the constructs, labels, and calibration differ.
- **Train fusion from in-sample component scores**: rejected because it leaks
  fitting behavior into the meta-model.
- **Auto-promote after training or review save**: rejected because training
  success is not release approval.
- **Write provider output into canonical MLS score/rank fields**: rejected because
  MLSSourcing owns those decisions.
- **Load mutable or hash-only untrusted artifacts**: rejected because integrity is
  not trust and executable serialization can run code.

## Implications for Future Features

Every future model feature must name the stable foundation requirements it
implements, supply RED-to-GREEN tests, preserve legacy compatibility, and resolve
only the open decisions within its approved scope. Experimental text/metadata
work and full multimodal release work remain separate task phases and release
claims.
