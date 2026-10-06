# Provider Contract Ownership

## Authority

SigLipModel is the ActVision provider and owns the wire contract. The
authoritative shape is the checked-in
[`contracts/actvision-v2.schema.json`](../../../contracts/actvision-v2.schema.json).
The cross-field identity, missing-modality, status, and semantic rules in
[`actvision_contract.py`](../../../actvision_contract.py) are equally required.
Synthetic fixtures demonstrate the contract; they are not real model outputs.

This feature does not replace, regenerate, or relax the schema. Legacy v1
contracts and artifacts remain unchanged.

## Provider Responsibilities

The provider:

- defines label, evidence, component-status, release, prediction, and feedback
  semantics;
- publishes compatible schema and semantic-validator changes before consumer
  enablement;
- binds responses to exact evidence and release identities;
- keeps UNKNOWN and missing/unavailable/failed states explicit;
- publishes deterministic synthetic fixtures and offline checks;
- records candidate, shadow, production, and retired transitions explicitly; and
- never writes canonical MLS economics, opportunity score, condition, or rank.

## Consumer Responsibilities

MLSSourcing:

- pins an independently approved provider contract bundle and release;
- validates JSON Schema and cross-field semantics;
- compares response evidence and release identity to the original request;
- stores provider results in shadow until its own integration is approved;
- owns economics, qualification, ranking, and canonical product writes; and
- rolls back by disabling consumption or pinning a previously approved compatible
  provider release.

## Compatibility Policy

The independently versioned bundle follows Semantic Versioning. PATCH releases
preserve every valid payload and existing meaning. MINOR releases may add
optional fields or enum behavior only when existing valid payloads and consumer
validation remain valid and semantics are not reinterpreted. A change to
required fields, canonicalization, label meaning, identity, status meaning, or
existing valid payloads is breaking, requires a MAJOR bundle version, and
requires a separately approved migration specification.

`contracts/manifest.json` pins raw-byte hashes for the schema, semantic
validator, generator, and every synthetic fixture. `contracts/VERSION` matches
its bundle version. Consumers pin the exact independently approved manifest,
validate it offline, and reject missing, additional, duplicated, reordered, or
byte-mismatched files. The manifest `source_commit` identifies the pre-bundle
provider commit containing those bytes rather than the self-referential commit
that publishes the manifest.

Provider-first rollout order:

1. update provider requirements and contract tests;
2. publish authoritative schema, semantic validator, fixtures, and exact hashes;
3. verify the provider release against that bundle;
4. let the consumer pin and validate the bundle offline;
5. enable consumer shadow behavior explicitly; and
6. enable any product behavior only under MLSSourcing approval.

Rollback reverses consumer enablement first and pins a previously approved
provider release/bundle. It does not rewrite historical requests, predictions,
feedback, or releases.

## Required Contract Checks

Contract validation MUST cover:

- `inference_request`, `prediction`, `feedback`, and `release`;
- canonical evidence identity and exact source snapshot;
- explicit component availability and requested-mode behavior;
- text PRESENT/ABSENT/UNKNOWN evidence rules;
- artifact and release compatibility;
- feedback idempotency and supersession; and
- rejection of cross-field identity or state mismatches.

The current fixture command is:

```powershell
python tools\export_actvision_fixtures.py --check
```

The current bundle command is:

```powershell
python tools\check_actvision_contract_bundle.py
```

A branch or deploy name MUST NOT be treated as a released contract identity.
