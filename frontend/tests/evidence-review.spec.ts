import { expect, test, type Page } from '@playwright/test';

const evidenceId = '11111111-1111-4111-8111-111111111111';
const groupId = '22222222-2222-4222-8222-222222222222';
const photoHash = 'a'.repeat(64);
const secondPhotoHash = 'b'.repeat(64);

async function mockPairedReview(
  page: Page,
  reviewCandidate = 'candidate-older',
  photos = true,
  revision: number | (() => number) = 2,
) {
  const manifests = photos ? [
    { sha256: photoHash, listing_key: 'listing-key-117' },
    { sha256: secondPhotoHash, listing_key: 'listing-key-117' },
  ] : [];
  const probes = photos ? [
    { sha256: photoHash, room: 'kitchen', decision: 'TARGET', strength: 50 },
    { sha256: secondPhotoHash, room: 'kitchen', decision: 'TARGET', strength: 50 },
  ] : [];
  const tags = photos ? [
    { sha256: photoHash, room: 'kitchen', exclusion: 'none', decision: 'NOT_TARGET', strength: null },
    { sha256: secondPhotoHash, room: 'kitchen', exclusion: 'none', decision: 'TARGET', strength: 50 },
  ] : [];
  await page.route('**/api/studio/paired-condition/status', route => route.fulfill({
    json: { token: 'review-token', candidate: { candidate_id: 'candidate-current' } },
  }));
  await page.route('**/api/studio/paired-condition/list', route => route.fulfill({
    json: {
      items: [{
        group_id: groupId,
        address: '17 Cedar Avenue',
        prior_mls: 'MLS-117',
        last_mls: null,
        reviewed: true,
        photo_events: [{ evidence_id: evidenceId, reviewed: true, stored: manifests.length, usable: manifests.length, kitchen_bath: false, best_coverage: false }],
      }],
      total: 1,
      review_batches: {},
    },
  }));
  await page.route('**/api/studio/paired-condition/property**', route => route.fulfill({
    json: {
      group_id: groupId,
      address: '17 Cedar Avenue',
      apn: '123-456',
      source: {},
      candidate: { candidate_id: 'candidate-current' },
      events: [{
        evidence_id: evidenceId,
        event_role: 'acquisition',
        listing_id: 'MLS-117',
        listing_key: 'listing-key-117',
        standard_status: 'Closed',
        revision: typeof revision === 'function' ? revision() : revision,
        raw_snapshot: { PublicRemarks: 'Updated kitchen.', ListPrice: 250000 },
        metadata_snapshot: { structured: { YearBuilt: 1986 } },
        photo_manifest: manifests,
        prediction: {
          status: 'completed',
          candidate_id: 'candidate-current',
          image: { decision: 'TARGET', strength: 50 },
          metadata: { decision: 'NOT_TARGET', strength: null },
          overall: { decision: 'TARGET', strength: 50 },
          photos: probes,
        },
        silver_reference: {
          image: { decision: 'NOT_TARGET', strength: null },
          metadata: { decision: 'NOT_TARGET', strength: null },
          overall: { decision: 'NOT_TARGET', strength: null },
        },
        photo_tags: tags,
        review: {
          image: { decision: 'TARGET', strength: 50, candidate_id: reviewCandidate },
          metadata: { decision: 'INSUFFICIENT_EVIDENCE', strength: null, candidate_id: reviewCandidate },
          overall: { decision: 'TARGET', candidate_id: reviewCandidate },
          notes: { text: 'Earlier review', candidate_id: reviewCandidate },
          photo_exclusions: { excluded: {} },
        },
      }],
    },
  }));
  await page.route('**/api/studio/paired-review/image**', route => route.fulfill({
    status: 200,
    contentType: 'image/jpeg',
    body: Buffer.from('fake-image'),
  }));
}

test('paired-condition provenance keeps model systems and earlier reviews distinct', async ({ page }) => {
  await mockPairedReview(page);
  await page.goto('/property-review');
  await expect(page.getByRole('heading', { name: '17 Cedar Avenue' })).toBeVisible();
  await expect(page.getByText('Current candidate: candidate-current')).toBeVisible();
  await expect(page.getByText('Earlier prediction review — not transferred')).toBeVisible();
  await expect(page.getByText('Reviewed candidate: candidate-older')).toBeVisible();
  await expect(page.getByText('Learned property image head')).toBeVisible();
  await expect(page.getByText('Not a vote over photo probes')).toBeVisible();
  await expect(page.getByText('Current per-photo model probes')).toBeVisible();
  await expect(page.getByText('Older automated-silver tags')).toBeVisible();
  await expect(page.getByText('Current probe · kitchen')).toHaveCount(2);
  await expect(page.getByText('2 dated rooms')).toHaveCount(0);
});

test('property image head omits stale warning when review matches current candidate', async ({ page }) => {
  await mockPairedReview(page, 'candidate-current');
  await page.goto('/property-review');
  await expect(page.getByRole('heading', { name: '17 Cedar Avenue' })).toBeVisible();
  await expect(page.getByText('Earlier prediction review — not transferred')).toHaveCount(0);
});

test('independent review and save and next bind evidence without a browser reviewer', async ({ page }) => {
  await mockPairedReview(page);
  let saved: Record<string, unknown> | undefined;
  await page.route('**/api/studio/paired-condition/correct', async route => {
    saved = route.request().postDataJSON();
    await route.fulfill({ json: { saved: true, revision: 3, dataset_unchanged: true } });
  });
  await page.goto('/property-review');
  await page.getByRole('tab', { name: 'Human review' }).click();
  await page.getByLabel('Overall decision').selectOption('INSUFFICIENT_EVIDENCE');
  await page.getByLabel('Photos decision').selectOption('TARGET');
  await expect(page.getByLabel('Photos target strength')).toBeVisible();
  await page.getByLabel('Photos target strength').fill('50');
  await page.getByLabel('Metadata decision').selectOption('NOT_TARGET');
  await expect(page.getByLabel('Metadata target strength')).toHaveCount(0);
  await expect(page.getByLabel('Overall decision')).toHaveValue('INSUFFICIENT_EVIDENCE');
  await page.getByLabel('Review notes').fill('Localized dated kitchen; other rooms reduce scope.');
  await page.getByRole('button', { name: 'Save and next' }).click();
  await expect.poll(() => saved).toBeTruthy();
  expect(saved).toEqual({
    evidence_id: evidenceId,
    expected_revision: 2,
    image: { decision: 'TARGET', strength: 50 },
    metadata: { decision: 'NOT_TARGET', strength: null },
    overall: 'INSUFFICIENT_EVIDENCE',
    notes: 'Localized dated kitchen; other rooms reduce scope.',
    candidate_id: 'candidate-current',
    excluded_photos: {},
  });
  expect(saved).not.toHaveProperty('reviewer');
});

test('independent review does not transfer stale candidate answers into a new correction', async ({ page }) => {
  await mockPairedReview(page, 'candidate-older');
  await page.goto('/property-review');
  await page.getByRole('tab', { name: 'Human review' }).click();
  await expect(page.getByLabel('Photos decision')).toHaveValue('INSUFFICIENT_EVIDENCE');
  await expect(page.getByLabel('Metadata decision')).toHaveValue('INSUFFICIENT_EVIDENCE');
  await expect(page.getByLabel('Overall decision')).toHaveValue('INSUFFICIENT_EVIDENCE');
  await expect(page.getByLabel('Review notes')).toHaveValue('');
  await expect(page.getByText('Earlier prediction review — not transferred')).toBeVisible();
});

test('independent review requires Cannot tell when no usable photos exist', async ({ page }) => {
  await mockPairedReview(page, 'candidate-current', false);
  await page.goto('/property-review');
  await page.getByRole('tab', { name: 'Human review' }).click();
  await expect(page.getByLabel('Photos decision')).toHaveValue('INSUFFICIENT_EVIDENCE');
  await expect(page.getByLabel('Photos decision')).toBeDisabled();
});

test('revision conflict keeps independent review values for retry', async ({ page }) => {
  let revision = 2;
  const attempts: Array<Record<string, unknown>> = [];
  await mockPairedReview(page, 'candidate-current', true, () => revision);
  await page.route('**/api/studio/paired-condition/correct', async route => {
    attempts.push(route.request().postDataJSON());
    if (attempts.length === 1) {
      revision = 3;
      await route.fulfill({
        status: 409,
        json: { error: 'This listing changed in another window. Reload before saving.' },
      });
    } else {
      await route.fulfill({ json: { saved: true, revision: 4, dataset_unchanged: true } });
    }
  });
  await page.goto('/property-review');
  await page.getByRole('tab', { name: 'Human review' }).click();
  await page.getByLabel('Review notes').fill('Keep this value after conflict.');
  await page.getByRole('button', { name: 'Save review' }).click();
  await expect(page.getByRole('alert')).toContainText('changed in another window');
  await expect(page.getByLabel('Review notes')).toHaveValue('Keep this value after conflict.');
  await page.getByRole('button', { name: 'Save review' }).click();
  await expect.poll(() => attempts.length).toBe(2);
  expect(attempts[0].expected_revision).toBe(2);
  expect(attempts[1].expected_revision).toBe(3);
});

test('API failure is an error and never an empty queue', async ({ page }) => {
  await page.route('**/api/studio/paired-condition/status', route => route.fulfill({
    status: 502,
    contentType: 'text/html',
    body: '<h1>Bad gateway</h1>',
  }));
  await page.goto('/property-review');
  await expect(page.getByRole('alert')).toContainText('502');
  await expect(page.getByText('No saved properties match this queue.')).toHaveCount(0);
});
