# Requirements Quality Checklist: ActVision v2 Provider Foundation

**Purpose**: A reviewer evaluates whether the specification is complete,
unambiguous, internally consistent, measurable, and safely bounded. These checks
review requirements, not implementation status.

**Created**: 2026-10-06

- [ ] CHK001 [Reviewer] Does every normative provider behavior have a stable `ML-`, `DATA-`, `TIME-`, `API-`, `SEC-`, `REL-`, or `OPS-` identifier?
- [ ] CHK002 [Reviewer] Are physical condition, modernization, acquisition fit, text signals, and product economics defined as separate constructs with no conflicting language?
- [ ] CHK003 [Reviewer] Is UNKNOWN distinguished unambiguously from ABSENT, missing input, unavailable artifacts, and execution failure in every relevant requirement?
- [ ] CHK004 [Reviewer] Do the evidence requirements identify every input whose change must create a new evidence identity, including exact remarks, structured facts, selected photo order, known image bytes, exclusions, era, and release?
- [ ] CHK005 [Reviewer] Do temporal requirements explicitly exclude later transactions, renovations, media, reviews, and outcomes from earlier acquisition snapshots?
- [ ] CHK006 [Reviewer] Is axis-specific human provenance required without implying that a property, photo, proposal, or one axis approves another?
- [ ] CHK007 [Reviewer] Are grouping and duplicate/alias rules strong enough to prevent one physical property or evidence item from crossing train, validation, and protected-test boundaries?
- [ ] CHK008 [Reviewer] Is every use of protected data excluded from feature fitting, component fitting, fusion, calibration, threshold selection, and model selection?
- [ ] CHK009 [Reviewer] Are dataset and run reproducibility requirements measurable from immutable identities rather than filenames, mutable paths, or branch names?
- [ ] CHK010 [Reviewer] Does the specification distinguish the current experimental text/metadata candidate from a full vision/text/structured/fusion release without implying approval?
- [ ] CHK011 [Reviewer] Are supported-class coverage, per-axis metrics, calibration, abstention, and required protected slices all addressed by measurable release criteria?
- [ ] CHK012 [Reviewer] Does missing required coverage block or narrow release scope rather than pass as zero or inherit a related class?
- [ ] CHK013 [Reviewer] Are candidate, shadow, production, retired, rollback, and retirement semantics explicit, authorized, auditable, and historically immutable?
- [ ] CHK014 [Reviewer] Is the authoritative role of `contracts/actvision-v2.schema.json` and `actvision_contract.py` stated consistently across the spec, plan, and contract notes?
- [ ] CHK015 [Reviewer] Are inference mode, component status, evidence/release binding, and no-silent-fallback requirements independently testable?
- [ ] CHK016 [Reviewer] Are feedback idempotency, conflicting reuse, supersession, workspace binding, and pending-review semantics independently testable?
- [ ] CHK017 [Reviewer] Are artifact trust, byte integrity, path containment, compatibility, and executable-deserialization risks all stated without treating a hash as authorization?
- [ ] CHK018 [Reviewer] Is the SigLipModel provider boundary separated clearly from MLSSourcing economics/ranking ownership and canonical MLS writes?
- [ ] CHK019 [Reviewer] Do non-goals explicitly prohibit training, inference, promotion, deployment, migration, paid work, and external-system modification by this foundation feature?
- [ ] CHK020 [Reviewer] Are open technical decisions paired with an owner/evidence gate and safe behavior until resolution rather than left as ambiguous placeholders?
- [ ] CHK021 [Reviewer] Do success criteria define observable pass/fail outcomes without inventing unsupported model-quality claims or numeric thresholds?
- [ ] CHK022 [Reviewer] Are legacy v1 compatibility and the provider-first/consumer-pinned migration rule stated consistently?
- [ ] CHK023 [Reviewer] Can each acceptance scenario be tested with synthetic or approved evidence without private data, a live service, or an undeclared side effect?
- [ ] CHK024 [Reviewer] Are worker bounds, idempotent recovery, sanitized failures, least privilege, and no stale fallback specified clearly enough for a later operations feature?
