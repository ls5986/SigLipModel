# Typed v2 model-assisted review

This branch combines the reviewed producer rebuild and semantic baseline with the
inventory home/source ledger, then adds single-property hosted OpenAI proposals.
It does not supply a trained v2 release or grouped/calibrated training orchestration.

## User flow

Home preserves imported source counts and links to `/studio`. The label queue opens
all matched properties, including source conflicts. Source conflicts remain visible
and cannot be bypassed by a model label. Unresolved source rows stay in the ledger.

Request one typed draft, check the result, load it into the review form, verify the
evidence, tweak mistakes, and save. Opening or refreshing pages makes no paid calls.
The worker shares the existing OpenAI daily budget, accepts only explicit single
requests and does not automatically retry interrupted paid calls. Set
`STUDIO_OPENAI_TYPED_ENABLED=true` only on the existing isolated worker after the
new branch is deployed. The default is disabled. No key belongs in browser code.

The property proposer uses actual remarks, allowlisted acquisition-time metadata
and existing current photo drafts with matching image hashes. It does not rerun
SigLIP or send additional photographs. Missing photo drafts are explicitly missing
visual evidence. This OpenAI proposal is not a calibrated v2 inference response.
The provisioned frozen semantic encoder remains distinct from trained task heads.

## Recommended approved labels

- Physical condition: C1_NEW, C2_LIKE_NEW, C3_WELL_MAINTAINED,
  C4_AVERAGE_FUNCTIONAL, C5_REHAB_NEEDED, C6_SEVERE_DISTRESS, UNKNOWN.
- Modernization: ORIGINAL, PARTIALLY_UPDATED, UPDATED, FULLY_REMODELED, UNKNOWN.
- Acquisition fit: TARGET, NOT_TARGET, UNKNOWN, independent of physical condition.
- Each of the 17 contract semantic tags: explicit PRESENT, ABSENT or UNKNOWN.
  PRESENT and model-proposed ABSENT need exact contrary/supporting remarks spans.
  Non-mention is UNKNOWN, not ABSENT.

Approve an axis only when its evidence supports the judgment. A 2026 build year
can inform acquisition fit but cannot prove condition or modernization. Lack of
photos does not discard verified text/structured examples. Models cannot certify
acquisition-era identity. No implicit legacy-condition conversion becomes approved
truth. Machine proposals remain `draft`; approval identifies the actual human and
retains the proposal model, policy, input hash and evidence identity in review
history. Corrections are explicit saves and do not start training or promotion.

Start with a small suggested batch spanning maintained/functional versus repair
needs, original versus updated, and target versus non-target examples. Review
models' drafts rather than manually recreate every tag. UNKNOWN remains valid.
Prioritize conflicts, changed evidence, missing modalities and unsupported axes.
Provisional planning coverage for a task head is at least five independent training
groups and two validation groups in each supported class, plus protected slices.
This is a review-planning floor, not a quality guarantee; do not force rare severe
distress/new-construction classes or unobserved text-tag negatives into the data.
Untrained classes must remain unavailable/UNKNOWN until adequate coverage exists.

## Read-only v2 preview

`/api/studio/v2/dataset/preview` counts only approved human typed reviews with current
evidence and verified source/era. It deduplicates physical groups, excludes conflicting
group labels and keeps any protected member out of training. Physical condition,
modernization, acquisition fit and each text tag have independent UNKNOWN counts.
Imported known targets and legacy reviews are never silently positive v2 truth.
Fingerprints change with label revisions, retractions, group/split changes or evidence
changes. This preview writes no dataset and freezes nothing.

V2 freeze/train routes fail closed rather than train the legacy acquisition-target
classifier. The v2 inference endpoint keeps its honest artifact/adaptor 503 gate.

## Deployment prerequisites and recovery

Existing staging only: Studio `srv-daukbp8u01pc7382dlng`, worker
`srv-db1t8o2jnfac73ehen70`, training project `tokcjofzlbjcqkuzhvrd`, workspace
`eb653547-fcfa-520b-ab4a-0f7359bda381`. Reviews/photos are non-disposable.
No production change, new billable service, protected-branch merge, training job or
bulk enqueue is requested by this branch.

The reviewed producer migration `20261002210000_actvision_v2_contracts.sql` was
applied on 2026-10-05 to this training project, recorded as migration history
`20261005201127` (`actvision_v2_contracts`). No base schema was recreated.
The user-supplied dashboard screenshot listed a physical backup at
2026-10-05 04:08:56 UTC; it was not restore-tested and excludes Storage objects.
Application recovery is redeployment of the prior recorded commits while retaining
the additive schema and existing reviews/photos. A managed database restore remains
an emergency action requiring separate approval and would discard later changes.

Before/after migration counts remained 618 examples and 13,271 photos, with unchanged
example, photo and existing human-review fingerprints. Runtime uses
`acq_studio_runtime`, the explicit training workspace and encrypted client
connections (`ssl_in_use=true`, `sslmode=require`). The private photo bucket
`acq-training-private` remains non-public. Pooler-to-database `pg_stat_ssl` is not
the client connection's TLS indicator.

Worker build: `python -B build_label_worker.py`; start:
`python -B hosted_label_worker.py`. Studio continues its existing hosted build/start.
Keep auto-deploy off. Verify deployed commit, login denial, restricted-role typed
history persistence and a single approved source record before any wider request.
The consumer migration/deployment and MLS shadow connection remain separate.

Remaining model work: grouped out-of-fold vision/semantic-text/structured training,
calibration, fusion, durable bundles, protected-slice thresholds and compatible
inference adapters. A paid draft assistant is useful immediately after deployment;
it does not complete these model-training pieces.

## Actual staging verification: 2026-10-05

- Studio service `srv-daukbp8u01pc7382dlng`: commit
  `a1566b85b205eb1ed066245c2bda0e99208d7158`, deploy
  `dep-db20h9ss728c73akh8ig`, live; health version `a1566b85b205`.
- Worker `srv-db1t8o2jnfac73ehen70`: commit
  `933abf915e5fbf507716766cd8a5ee08f132a686`, deploy
  `dep-db20g1rncjis73b8begg`, live; running commit verified in the instance.
  Both use the feature branch, with auto-deploy off; no merge was performed.
- Authenticated home, Studio, source ledger, MLS validation, status, feedback inbox
  and release list returned 200. Unauthenticated v2 capabilities returned 401.
  An intentionally stale save returned 409 without writing a review.
- The all-record queue returned 463 matched listings. Imported ledger totals are
  618 source rows / 551 physical groups; these are not approved human task labels.
- Real source/era-verified listing `1145875648` had 21 retained photos and actual
  remarks; a retained thumbnail returned 200 image/jpeg. No evidence was fabricated.
- An explicitly queued single OpenAI typed request was consumed by the hosted worker
  but failed before saving any proposal. The original code retained only a generic
  error, so its cause is unknown. No successful model proposal is claimed.
  Today's two shared call slots are consumed; no automatic retry or budget increase
  occurred. The newer worker records safe failure stage/type/status, without provider
  bodies or keys, and derives offsets only from unique exact verbatim snippets.
  Those diagnostics have not been verified with another paid request.
- Worker typed/OpenAI flags are true; Copilot false; paused false; daily limit 2.
  Pinned semantic checkpoint all-MiniLM-L6-v2 revision
  `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` verified at runtime.
  Provisioning a frozen encoder is not training task heads or a fusion bundle.
- Corrected read-only v2 preview returned 200: 0 approved typed review rows,
  0 independent train/validation/protected-test groups, all per-class/per-tag approved
  coverage counts 0, and 7 actual legacy/draft review representations.
  Empty imported review envelopes are no longer counted as reviews.
  Missing-modality totals among the empty approved set cannot describe all imports.
- Existing source/era and legacy human review records remain non-disposable.
  No human label was approved on the user's behalf; review_events remains 0.
  Human Save & Next/reload history, Advanced-tag/standout retention, and real model-backed
  feedback remain unverified in this deployment. Disposable/local tests cover their
  underlying persistence, contracts and guards.
- 129 focused Python tests passed before the audit-counter refinement; the final
  typed-assistance suite passed all 19 cases, including the three added audit cases.
  Updated full browser regression remains unrun (local Chromium installation failed).
- MLS dev consumer was not deployed or migrated in this action. Its default-off shadow
  integration, migration 0046, service-to-service secret configuration, compatible
  trained bundle/adapters and protected-slice acceptance remain prerequisites.
  Studio ACTVISION_SERVICE_TOKEN is missing; no credential was disclosed or changed.
  No new billable infrastructure, training, freeze, promotion or canonical MLS write
  occurred. The dashboard also showed a quota restriction warning for 26 October;
  billing and plan settings were left unchanged.

The next bounded live test is an explicit single-property retry after the shared UTC
daily budget resets, using the deployed diagnostics. After a valid draft exists,
the reviewer verifies/tweaks and explicitly approves it; the approved typed preview
can then demonstrate persistence and label revision invalidation. Start with a small,
varied independently grouped batch, filling supported positive/negative classes and
missing-modality examples. The planning floor above is not a model-quality guarantee.
