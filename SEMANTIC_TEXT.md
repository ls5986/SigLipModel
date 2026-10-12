# Supervised semantic listing text

This feature adds an explicit offline encoder path. It does not download weights,
start training, deploy inference, or replace the legacy default automatically.
Provision an audited SentenceTransformer checkpoint (a compact English encoder
such as all-MiniLM-L6-v2 is a candidate to evaluate, not a validated MLS model).
Install requirements-semantic-text.txt in the candidate-training runtime.

Compute the checkpoint directory hash by hashing each regular file in sorted
relative-path order: UTF-8 relative POSIX path, NUL byte, then file bytes.
The checkpoint directory must be trusted and immutable. Bundles retain its
hash and path; loading on another host requires that same checkpoint and a
reconciled local path. Pickled candidate bundles are trusted artifacts only.

```python
from semantic_text import SemanticTextEncoder
from text_model import TextModel
from workbench_training import train_candidate
encoder = SemanticTextEncoder(checkpoint_directory, checkpoint_sha256)
text_component = TextModel.fit(remarks, approved_text_labels, encoder=encoder)
legacy_candidate = train_candidate(frozen_grouped_rows, text_encoder=encoder)
```

The encoder is frozen, CPU-only and local-files-only with remote code disabled.
Long descriptions are token-chunked and length-weighted, so trailing remarks
are included. Structured fields remain separate. Contextual vectors feed
supervised heads, not nearest-known-target similarity. OOF metadata heads use
only each fitting fold; validation/protected labels never fit the fusion head.
UNKNOWN tag labels stay unassessed, never negatives. Blank remarks produce
UNKNOWN in the separate text component. No regex negation rule is treated as
human truth. Context/negation comprehension must be evaluated on reviewed MLS
examples; embeddings alone do not guarantee correct interpretation.

Predicted semantic tags currently retain null snippets/offsets: a document
classifier cannot justify exact evidence spans. Human snippet validation is
unchanged. Evidence-grounded span extraction, calibrated abstention, grouped
v2 orchestration, release adapter and actual hosted loading remain required.
The legacy candidate still predicts acquisition target; it is not a trained
v2 physical-evidence model. The 503 v2 inference artifact gate stays intact.

Tests use a fake encoder to check plumbing, missing/tampered artifact gates,
long-text coverage, serialization, explicit negatives and grouped OOF wiring.
They do not demonstrate pretrained encoder accuracy. No real training or paid
model calls are performed by these tests.
