export type DecisionName = 'TARGET' | 'NOT_TARGET' | 'INSUFFICIENT_EVIDENCE';
export type Decision = { decision: DecisionName; strength?: number | null; candidate_id?: string; evidence_sha256?: string };
export type Candidate = { candidate_id: string; [key: string]: unknown };
export type PredictionPhoto = Decision & { sha256: string; room?: string; [key: string]: unknown };
export type Prediction = {
  status?: string;
  candidate_id?: string;
  image?: Decision;
  metadata?: Decision;
  overall?: Decision;
  photos?: PredictionPhoto[];
  excluded_photos?: Array<{ sha256: string; reason: string }>;
  [key: string]: unknown;
};
export type PhotoTag = Decision & { sha256: string; room?: string; exclusion?: string };
export type HumanReview = {
  image?: Decision;
  metadata?: Decision;
  overall?: Decision;
  notes?: { text?: string; candidate_id?: string; evidence_sha256?: string };
  photo_exclusions?: { excluded?: Record<string, string> };
  [axis: string]: unknown;
};
export type PhotoManifestItem = { sha256: string; listing_key?: string; [key: string]: unknown };
export type PairedEvent = {
  evidence_id?: string;
  event_role: string;
  listing_id?: string;
  listing_key?: string;
  standard_status?: string;
  revision?: number;
  raw_snapshot?: Record<string, unknown>;
  metadata_snapshot?: Record<string, unknown>;
  photo_manifest?: PhotoManifestItem[];
  prediction?: Prediction | null;
  silver_reference?: { image?: Decision; metadata?: Decision; overall?: Decision } | null;
  photo_tags?: PhotoTag[];
  review?: HumanReview | null;
  [key: string]: unknown;
};
export type PairedProperty = {
  group_id: string;
  address: string;
  apn?: string;
  unit?: string;
  source?: Record<string, unknown>;
  candidate?: Candidate | null;
  events: PairedEvent[];
  warning?: string;
};
export type PhotoEventSummary = {
  evidence_id: string;
  reviewed: boolean;
  stored: number;
  usable: number;
  kitchen_bath: boolean;
  best_coverage: boolean;
};
export type PairedQueueItem = {
  group_id: string;
  address: string;
  prior_mls?: string | null;
  last_mls?: string | null;
  reviewed?: boolean;
  photo_events?: PhotoEventSummary[];
  [key: string]: unknown;
};
export type PairedQueue = {
  items: PairedQueueItem[];
  total: number;
  review_batches?: Record<string, unknown>;
};
export type PairedStatus = {
  token?: string;
  candidate?: Candidate | null;
  [key: string]: unknown;
};
export type CorrectionRequest = {
  evidence_id: string;
  expected_revision: number;
  image: Decision;
  metadata: Decision;
  overall: DecisionName;
  notes: string;
  candidate_id?: string;
  excluded_photos: Record<string, string>;
};
export type CorrectionResponse = { saved: true; revision: number; dataset_unchanged: true };

let token = '';

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/studio/${path}`, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...(body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Review-Token': token },
      body: JSON.stringify(body),
    }),
  });
  const type = response.headers.get('content-type') || '';
  if (!type.includes('application/json')) {
    if (response.redirected || new URL(response.url).pathname === '/login') location.assign('/login');
    throw new Error(`The review service returned ${response.status || 'an invalid response'}. Try again.`);
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  if (typeof data.token === 'string') token = data.token;
  return data as T;
}

export const loadStatus = () => request<PairedStatus>('paired-condition/status');
export const loadQueue = () => request<PairedQueue>('paired-condition/list');
export const loadProperty = (groupId: string) =>
  request<PairedProperty>(`paired-condition/property?${new URLSearchParams({ id: groupId })}`);
export const saveCorrection = (payload: CorrectionRequest) =>
  request<CorrectionResponse>('paired-condition/correct', payload);

export function mediaUrl(id: string, thumbnail?: boolean): string;
export function mediaUrl(evidenceId: string, sha256: string, thumbnail?: boolean): string;
export function mediaUrl(evidenceId: string, sha256OrThumbnail: string | boolean = false, thumbnail = false): string {
  if (typeof sha256OrThumbnail === 'boolean') {
    return `/api/studio/${sha256OrThumbnail ? 'thumbnail' : 'image'}?id=${encodeURIComponent(evidenceId)}`;
  }
  return `/api/studio/paired-review/image?${new URLSearchParams({
    evidence: evidenceId,
    photo: sha256OrThumbnail,
    ...(thumbnail ? { thumbnail: '1' } : {}),
  })}`;
}

// Transitional exports keep the existing component type-safe until Task 3 replaces it.
export type QueueItem = { id: string; address: string; city?: string; image_count: number; hero_image_id?: string; status: string; human_score?: number; needs_photo_match?: boolean; review_complete?: boolean; evidence_mode?: string; source_role?: string };
export type Review = { revision?: number; status?: string; target_fit?: string; target_score?: number; reason?: string; condition_label?: string; physical_condition?: string; modernization_state?: string; confidence?: string; evidence_source?: string };
export type ImageEvidence = { id: string; sha256: string; room?: string; room_source?: string; provider_context?: string; provider_context_source?: string; provider_description?: string; condition_draft?: string; effective?: { room?: string; context?: string; condition_label?: string; local_score?: number; gpt_scope_score?: number; features?: Record<string, unknown> }; review?: { revision?: number; status?: string; room?: string; condition_label?: string; physical_condition?: string; modernization_state?: string; notes?: string }; suggestions?: Array<{ source?: string; room?: string; context?: string; condition_label?: string; features?: Record<string, unknown> }>; synthetic_evidence?: { excluded?: boolean; reasons?: string[] }; selection?: { included?: boolean; reason?: string }; split?: string; warnings?: string[] };
export type Detail = { property: { id: string; address: string; city?: string; year_built?: number; property_type?: string; metadata?: Record<string, unknown>; review?: Review; mls_remarks?: string; synthetic_evidence?: unknown }; images: ImageEvidence[]; coverage?: Record<string,string>; historical_source?: { source_rows?: string[]; source?: Record<string, unknown>; mls_listing?: Record<string, unknown>; evidence_hash?: string; acquisition_status?: string; block_reason?: string; review?: unknown; photo_coverage?: string }; capabilities?: { assessment?: boolean; training?: boolean } };
export const api = request;
