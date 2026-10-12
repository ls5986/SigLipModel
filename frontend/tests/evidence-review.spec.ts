import { expect, test, type Page } from '@playwright/test';

const evidenceId = '11111111-1111-4111-8111-111111111111';
const groupId = '22222222-2222-4222-8222-222222222222';
const photoHash = 'a'.repeat(64);
const secondPhotoHash = 'b'.repeat(64);

async function mockPairedReview(page: Page, reviewCandidate = 'candidate-older') {
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
        photo_events: [{ evidence_id: evidenceId, reviewed: true, stored: 2, usable: 2, kitchen_bath: false, best_coverage: false }],
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
        revision: 2,
        raw_snapshot: { PublicRemarks: 'Updated kitchen.', ListPrice: 250000 },
        metadata_snapshot: { structured: { YearBuilt: 1986 } },
        photo_manifest: [
          { sha256: photoHash, listing_key: 'listing-key-117' },
          { sha256: secondPhotoHash, listing_key: 'listing-key-117' },
        ],
        prediction: {
          status: 'completed',
          candidate_id: 'candidate-current',
          image: { decision: 'TARGET', strength: 50 },
          metadata: { decision: 'NOT_TARGET', strength: null },
          overall: { decision: 'TARGET', strength: 50 },
          photos: [
            { sha256: photoHash, room: 'kitchen', decision: 'TARGET', strength: 50 },
            { sha256: secondPhotoHash, room: 'kitchen', decision: 'TARGET', strength: 50 },
          ],
        },
        silver_reference: {
          image: { decision: 'NOT_TARGET', strength: null },
          metadata: { decision: 'NOT_TARGET', strength: null },
          overall: { decision: 'NOT_TARGET', strength: null },
        },
        photo_tags: [
          { sha256: photoHash, room: 'kitchen', exclusion: 'none', decision: 'NOT_TARGET', strength: null },
          { sha256: secondPhotoHash, room: 'kitchen', exclusion: 'none', decision: 'TARGET', strength: 50 },
        ],
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
