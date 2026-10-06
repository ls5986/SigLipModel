# ActVision Contract Bundle Changelog

All notable changes to the versioned ActVision contract bundle are recorded here.
Bundle versions follow Semantic Versioning independently of the `actvision-v2`
wire version.

## [1.0.0] - 2026-10-06

- Published the initial stable bundle for wire contract `actvision-v2` and
  taxonomy `actvision-labels-v2`.
- Pinned the authoritative schema, semantic validator, fixture generator, and
  all seven synthetic fixtures by raw-byte SHA-256.
- Added a deterministic offline bundle checker.
- Recorded source provenance at provider commit
  `68a91e86c13018fed86bb5538cf44002d55cc1fe`.

This release versions the existing contract bytes; it does not change schema,
fixture, runtime, or model behavior.
