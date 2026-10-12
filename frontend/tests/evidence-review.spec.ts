import { expect, test } from '@playwright/test';

test('shows evidence provenance separately and saves a revisioned human review', async ({ page }) => {
  const detail = {
    property: { id: 'listing-17', address: '17 Cedar Avenue', city: 'Columbus', year_built: 1986, property_type: 'Single Family', metadata: {}, review: { revision: 2, status: 'approved', target_score: 3, target_fit: 'unsure', confidence: 'medium', reason: 'Earlier review' }, mls_remarks: 'Updated kitchen.' },
    images: [{ id: 'listing-17:photo-1', sha256: 'a'.repeat(64), room: 'kitchen', room_source: 'SigLIP suggestion', provider_context: 'subject', provider_context_source: 'MLS description', provider_description: 'Kitchen photo', condition_draft: 'average', effective: { room: 'kitchen', context: 'subject', features: { dated_cabinets: true } }, suggestions: [{ source: 'SigLIP', condition_label: 'average' }], review: { status: 'approved', condition_label: 'average' }, selection: { included: true, reason: 'Subject interior' }, split: 'learning' }],
    coverage: { kitchen: 'confirmed' }, historical_source: { source_rows: ['source-row-1'], mls_listing: { ListingId: 'MLS-117', StandardStatus: 'Closed', ListPrice: '250000', CloseDate: '2025-02-01' }, evidence_hash: 'e'.repeat(64), acquisition_status: 'prior_acquisition_candidate', photo_coverage: 'confirmed' }, capabilities: { assessment: false, training: false },
  };
  await page.route('**/api/studio/review-queue**', route => route.fulfill({ json: { token: 'review-token', items: [{ id: 'listing-17', address: '17 Cedar Avenue', city: 'Columbus', image_count: 1, hero_image_id: 'listing-17:photo-1', status: 'reviewed', needs_photo_match: false }], counts: { todo: 1, verified: 1 }, total: 1 } }));
  await page.route('**/api/studio/property**', route => route.fulfill({ json: detail }));
  await page.route('**/api/studio/thumbnail**', route => route.fulfill({ status: 200, contentType: 'image/jpeg', body: Buffer.from('fake-image') }));
  let saved: Record<string, unknown> | undefined;
  await page.route('**/api/studio/review', async route => { saved = route.request().postDataJSON(); await route.fulfill({ json: { revision: 3, saved: true } }); });

  await page.goto('/property-review');
  await expect(page.getByRole('heading', { name: '17 Cedar Avenue' })).toBeVisible();
  await expect(page.getByText('Saved property evidence')).toBeVisible();
  await expect(page.getByText('Human reviewed', { exact: true })).toBeVisible();
  await expect(page.getByText('Not training approved')).toBeVisible();
  await page.getByRole('tab', { name: 'Model outputs' }).click();
  await expect(page.getByText('SigLIP Suggestion', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Human review' }).click();
  await page.getByLabel('Target fit score').selectOption('4');
  await page.getByLabel('Reason').fill('The visible kitchen finishes support a good fit.');
  await page.getByRole('button', { name: 'Save review' }).click();
  await expect.poll(() => saved?.expected_revision).toBe(2);
  expect(saved?.target_score).toBe(4);
  expect(saved?.evidence_source).toBe('both');
});
