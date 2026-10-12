# Review clarity update

Local implementation, not pushed or deployed.

- Keep trained scoring and persisted dataset unchanged.
- Separate current model prediction, saved human assessment, and correction form.
- Suggest overall TARGET from either axis; keep manual overall override.
- Default missing target strength to 50, preserve existing strengths.
- Restore stored public/private remark signals with source and negation labels. Hide full remarks in expandable sections. Signals are evidence, not model explanations.
- Show net dollar and percentage reduction, last recorded reduction and timestamp when available. Do not infer reduction count or substitute missing history with zero.
- Preserve authentication, CSRF, revisions, photo exclusions, save/next and unsaved-edit guards.

Remaining data audit: verify original Excel keyword column fidelity and provider history coverage. No new provider calls, re-training or schema changes in this update.

## Additional implemented UI
- Hide both-MLS-missing records by default; toggle to show them. One-sided records remain reviewable. No archive/delete writes.
- Show remarks and structured model predictions separately, combination rule and all 18 structured inputs with missingness. No invented feature attribution.
- Manual ephemeral comp calculator: purchase/resale, grade, distance, reason, rehab and other costs. No automated opportunity score or comp fetch.
- Shared `opportunity_math.js` implements versioned gross upside, discount-to-value and cost-aware margin. Identical inline copy avoids adding a hosted route.
- Assign-MLS workflow remains future backend work, not a nonfunctional button.

- All / Not reviewed / Reviewed filters, queue counts and review badges. Reviewed uses existing backend property-level review completeness. Save refreshes queue status; save-next follows the pre-save filtered order. Search and missing-MLS filters compose with review status.
