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

The producer migration `20261002210000_actvision_v2_contracts.sql` is missing from
actual staging migration history and must precede typed review writes. Inspect
current migration history and a usable managed backup/recovery point first. Apply
only the missing reviewed migration transactionally with administrative credentials;
continue runtime under its restricted workspace role. Do not recreate base schema.
If migration fails, roll back the transaction. If application verification fails,
redeploy the prior recorded Studio/worker commits while retaining additive schema
and all existing reviews/photos. A verified database recovery point is still required
before applying a migration to this non-disposable project.

Worker build: `python -B build_label_worker.py`; start:
`python -B hosted_label_worker.py`. Studio continues its existing hosted build/start.
Keep auto-deploy off. Verify deployed commit, login denial, restricted-role typed
history persistence and a single approved source record before any wider request.
The consumer migration/deployment and MLS shadow connection remain separate.

Remaining model work: grouped out-of-fold vision/semantic-text/structured training,
calibration, fusion, durable bundles, protected-slice thresholds and compatible
inference adapters. A paid draft assistant is useful immediately after deployment;
it does not complete these model-training pieces.
