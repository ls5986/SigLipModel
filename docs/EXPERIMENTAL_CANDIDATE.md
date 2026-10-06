# Hosted experimental candidate flow

Studio can train a provisional description and metadata candidate from explicitly
selected, unreviewed AI drafts. Approved human corrections take priority. Training
and predictions are separate from human reviews, the release registry and MLS
production intelligence.

The experiment uses a pinned frozen semantic encoder and allowlisted acquisition
metadata with supervised logistic heads. It does not retrain the image encoder,
use photographs as model inputs, calibrate probabilities or train late fusion.
Supported historical listing descriptions and facts remain usable while photo
certification is pending. This does not change photo certification or human review
statuses. Source conflicts and protected physical/image groups stay excluded.

Tasks need sufficient independent groups in multiple known classes. Unknown labels
are masked. Rare classes are excluded from fitting rather than relabeled as
negatives, and acquisition fit needs both target and non-target examples.
Held-out agreement with AI drafts is not measured human accuracy.

## Use the model

Open **Datasets & models** in Studio to see **Train & test**. Explicitly select the
unreviewed draft experiment and use **Train experimental candidate**. The request
persists through refreshes and the hosted worker processes it when coverage is
ready. Open a property in **Review properties**, return to **Datasets & models**,
and use **Analyze the open property**. Predictions are saved separately from
labels and disclose whether the property group appeared in training or validation.

Correct drafts in Review properties when useful, then start a new explicit
experiment. Review saves never silently retrain a candidate. Training saves model
coefficients, encoder identity, selected label provenance and a dataset fingerprint
in the training workspace. Inputs and labels are checked again before the bundle
is committed. A stale training lease can retry the bounded CPU fit; an interrupted
final status write recovers from an already-saved bundle. Paid draft calls never
retry implicitly.

## Engineering checks and remaining work

Focused checks cover cloud snapshots, assisted drafts, durable batches, contracts
and hosted worker behavior. The experimental snapshot skips expensive imported
legacy history while preserving live reviews and group protection. Source-only
confirmations without typed labels do not become training examples. Preflight
failures retain non-secret stage, exception type and frame location diagnostics.

Still separate: trained vision components, independently evaluated and calibrated
fusion, human validation coverage, compatible release adapters and the authenticated
MLS dev shadow and feedback connection. This experimental path does not promote
models into production or merge protected branches.
