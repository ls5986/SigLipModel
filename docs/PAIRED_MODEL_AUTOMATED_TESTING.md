# Paired model update and automatic checks

Automated scan of 578 records is deterministic evidence extraction, not independent AI/human review. Existing protected results informed this repair, so this is development feedback, not a fresh unbiased accuracy claim.

Every new training fits photo, text, structured and overall fusion heads using frozen train groups. A new metadata fusion head uses grouped out-of-fold text/structured predictions. Validation selects between remarks-first, remarks-only, legacy union and learned metadata fusion, penalizing uncertainty. Test labels do not fit heads or select the approach.

The new artifact stores component evaluations, validation selection and old-versus-new comparison using the same new frozen labels. The active development candidate is updated only when validation metadata and overall balanced agreement including abstentions do not decrease. Existing artifacts are preserved. No production promotion.

The GitHub Paired model regression checks workflow runs synthetic model tests and remark evidence tests on pushes/PRs touching model code. No credentials or private records are sent to Actions. Local command: `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m pytest -q test_paired_condition_models.py test_paired_condition_evidence.py`.

Every UI Train next version operation performs evaluation and comparison automatically. Model results and dataset shows the selected recipe and comparison. Corrections from protected groups remain evaluation labels, not training rows.

The text head now combines pinned semantic embeddings, at most 256 TF-IDF word/bigram features and explicit condition signals. Each grouped training fold fits its own vocabulary and IDF. Held-out words never create features. The serialized artifact stores the vocabulary and IDF for identical online transforms; older semantic-only artifacts remain readable. This improves agreement with silver references, which are partly rule-generated, and must not be described as independent AI accuracy.

No meaningful remarks or supported structured fields means metadata abstains, including comparable-only boilerplate. Empty prediction batches are supported. Predictions expose the strongest present linear word/structured associations, clearly distinguished from quoted listing evidence and causal explanations. The UI explains the actual selected metadata recipe rather than the obsolete OR rule.
