# React condition review and staged intake

## Goal
Move the paired condition-review experience into a same-origin React 19/TypeScript/Vite application in SigLipModel. Keep the Python service, authentication, and workspace authorization. Add a user-driven, staged MLS lookup/review path only when exact listing identity is confirmed. A saved review must remain separate from training eligibility and from Opportunity creation.

## Repository and data constraints
- Start from `origin/develop` and leave open PRs #32 and #33 untouched. They contain unfinished workbook/paired-review work; review their contracts before adding overlapping behavior.
- Use MLSsourcing `develop` as a read-only UX/accessibility reference.
- Reuse `acq_training` listing events, evidence snapshots, photo retention, revisioned review events, and workspace state where their existing contracts fit. Do not create parallel sources of truth or mutate frozen datasets.
- The connected `Model Training` Supabase project has populated training/review data and no development branch. This change will not apply migrations or write to that shared database. Any required DDL is committed as a migration and reported as requiring an isolated DEV database before application.
- No Render connection is available in this cloud session, and Trestle credentials are not available to local tests. Provider integration will be tested with deterministic transport fixtures; real-provider/live acceptance remains a merge gate.

## Target workflow
1. React reviewer opens the saved review queue and property detail on the same origin. Existing auth, CSRF/review token, and media-retention checks remain server-side.
2. Detail view keeps current property-level model outputs, current per-photo probes, older automated-silver tags, deterministic remarks policy, and saved human review in separately labeled panels. Review provenance includes candidate and evidence snapshot identities; stale review is never copied forward.
3. Optional lookup accepts an MLS/listing ID or address. The backend queries the configured development provider. Ambiguous matches require explicit selection; there is no automatic selection or persistence from search alone.
4. After explicit selection, stage an idempotent listing/evidence snapshot in existing workspace-scoped structures. Validate transaction/status, record immutable source facts and retained-media provenance, and leave the record outside every frozen dataset.
5. The reviewer records Photos, Metadata, Overall, scope, strength, and a reason against the staged evidence revision. Save and Save → Next use expected-revision checks. Condition feedback does not create an Opportunity or training approval.
6. A separately permission-gated action may append training-eligibility authorization for a reviewed, evidence-bound record. It only marks eligibility for a future versioned dataset build; it never mutates a frozen dataset, starts training, rescores properties, or promotes a model.
7. Comparable selection is an independent evidence section. Show only asking price, sold price, and `sold - asking` when both are known, plus a factual reason based on stored fields. Missing values remain “Not established.” Comps and price delta never change condition decisions.

## Implementation plan
- Phase A: add Vite/React build and same-origin asset/API routing without removing existing routes; add typed API client, auth bridge, and paired-review/evidence page patterned on MLSsourcing. Preserve existing Python review route as rollback.
- Phase B: implement exact-ID/address lookup, ambiguity selection, staged intake/evidence retrieval, revisioned save/next, and separate authorization action using existing backend/provider/data contracts. Reuse existing tables/state where possible; add only a reviewed migration if an existing contract cannot represent the event safely.
- Phase C: add comparable candidate selection and factual delta/reason panel, isolated from condition state.
- Phase D: add backend/frontend/accessibility tests, local browser checks, security review, and documentation. Run real DEV provider/database/browser acceptance only when isolated DEV credentials and deployment access are available.

## Non-goals and safety
No production configuration, model behavior, live predictions, existing reviews, frozen datasets, Opportunity membership, broad imports, bulk scoring, or training runs are changed. Do not use later resale outcomes as acquisition-time features or labels. Do not claim live acceptance from mocked provider tests.

## Merge gate
Open a develop PR after local gates pass. Leave it unmerged if isolated DEV provider/database/browser acceptance, required CI, or review is unavailable. Do not apply migrations to the shared Model Training project or merge into main/production.

## Cloud branch implementation status

The first implementation slice adds a React 19/TypeScript/Vite evidence viewer at `/property-review` and keeps `/review` on the current HTML page. It uses the existing same-origin queue, property, photo, and revisioned review endpoints. The viewer separates retained photos, automated outputs, saved human decisions, listing facts, and source provenance. It does not expose training approval or Opportunity actions.

The local browser test stubs the queue and detail endpoints and verifies the save request carries the current expected revision. Python route tests verify authenticated app and asset delivery, asset path confinement, and existing authentication behavior. No schema changes or shared Supabase writes are included.

The staged MLS search/intake, separately authorized training eligibility event, and comparable-selection section remain unimplemented. They depend on a confirmed development provider/data contract and isolated development database. The connected Model Training project is populated and has no development branch, so it was not used for writes. The live provider, hosted browser, and Render deployment acceptance remain required before merging the broader workflow.

## Paired-condition integration verification

The React viewer is now integrated on a branch descended from the deployed paired-condition backend rather than the legacy Studio review contract. It uses the paired-condition status, queue, property, and correction endpoints; presents Photos, Metadata, and Overall as independent TARGET / NOT TARGET / Cannot tell decisions; and leaves reviewer identity bound to the authenticated server session.

Verified locally on 2026-10-12:

- reproducible `npm ci` using the configured package mirror;
- production TypeScript/Vite build;
- six Playwright browser tests covering candidate provenance, separate current/silver photo systems, independent decisions, no-photo handling, revision conflicts, Save → Next payloads, and non-JSON API failures;
- 81 focused Python hosted and paired-condition tests;
- clean `git diff --check` for the React integration range.

Verified read-only against the authenticated development deployment:

- paired-condition status, 551-item queue, and property detail endpoints returned successfully;
- both stored-photo and no-photo evidence paths were present;
- repeated property reads returned stable evidence identities;
- 5091 El Arbol retained current candidate `f9bac37b-3ac4-4b64-9b54-cd9581bd226a` separately from human-review candidate `beb45c78-052b-4905-a2bf-9fba266e8c16`;
- current per-photo probes and older automated-silver tags remained separate systems.

Write acceptance remains blocked because the development deployment uses the populated shared Model Training database and has no isolated writable development branch. No review was saved, no migration was applied, and no dataset or training state changed. Keep the integration PR draft and do not merge until an isolated DEV save/reload/Save → Next test and required CI/review pass.
