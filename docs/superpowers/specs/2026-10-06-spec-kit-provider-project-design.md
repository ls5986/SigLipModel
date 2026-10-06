# SigLipModel Spec Kit Provider Project Design

## Intent

Convert SigLipModel into the authoritative Spec Kit project for the ActVision ML
provider. Preserve current research and hosted workflows while adding explicit
ML principles, standard feature artifacts, a versioned contract bundle, and
offline compatibility validation for consumers.

## Boundaries

SigLipModel owns:

- ActVision label ontology and unknown semantics;
- point-in-time dataset construction and protected splits;
- model training, calibration, evaluation, artifacts, and releases;
- inference and feedback provider behavior;
- the authoritative ActVision wire contract and fixtures.

MLSSourcing owns product economics, opportunity qualification/ranking, and the
consumer-side shadow integration. SigLipModel must never write canonical MLS
opportunity scores or ranks.

## Project layout

```text
.specify/
  memory/constitution.md
  templates/
  scripts/
specs/
  001-actvision-v2-provider-foundation/
    spec.md
    plan.md
    research.md
    data-model.md
    quickstart.md
    contracts/README.md
    checklists/requirements.md
    tasks.md
contracts/
  actvision-v2.schema.json
  fixtures/
  VERSION
  CHANGELOG.md
  manifest.json
```

The existing schema remains the source of truth. The version and manifest make
that source consumable across repository boundaries without treating branch
names as release identities.

## Contract bundle

`contracts/manifest.json` records:

- provider repository and immutable source commit;
- wire and taxonomy versions;
- semantic contract version;
- schema path and SHA256;
- fixture paths and SHA256 values;
- compatibility policy;
- generator and checker commands.

`tools/export_actvision_fixtures.py --check` continues to prove fixture semantic
validity. A new bundle checker proves the manifest hashes match raw checked-in
bytes. Publishing a compatible provider change precedes consumer enablement.

## Governance

The constitution requires:

- no future-event leakage into acquisition-time evidence;
- exact evidence, dataset, code, and release identities;
- unknown and missing modalities remain unknown;
- label provenance is axis-specific;
- physical condition, modernization, acquisition fit, and economics remain separate;
- group isolation and protected-test integrity;
- exact human-per-axis release gates;
- explicit candidate, shadow, production, and retired transitions;
- no implicit training or promotion from review saves;
- reproducible, bounded, and observable workers.

## Verification

Tests verify:

- the Spec Kit root, constitution, active provider feature, and standard artifacts exist;
- ML requirements and release gates have stable identifiers;
- the bundle manifest covers the schema and every fixture exactly once;
- hashes match raw bytes;
- fixture export checks and semantic validators still pass;
- CI executes both project and bundle validation.

This conversion does not train, promote, deploy, or change a model. It does not
modify Supabase, Render configuration, MLS consumer behavior, or production data.
