import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Images, RefreshCw, Search, ShieldCheck } from 'lucide-react';
import {
  loadProperty,
  loadQueue,
  loadStatus,
  mediaUrl,
  saveCorrection,
  type Decision,
  type DecisionName,
  type PairedEvent,
  type PairedProperty,
  type PairedQueueItem,
} from './api';

type Tab = 'evidence' | 'models' | 'review' | 'source';
type ReviewDraft = {
  image: DecisionName;
  imageStrength: number;
  metadata: DecisionName;
  metadataStrength: number;
  overall: DecisionName;
  notes: string;
  excluded: Record<string, string>;
};

const pretty = (value: unknown) =>
  value == null || value === ''
    ? 'Not established'
    : String(value).replaceAll('_', ' ').replace(/\b\w/g, character => character.toUpperCase());

function Badge({ children, tone = 'neutral' }: {
  children: React.ReactNode;
  tone?: 'neutral' | 'blue' | 'green' | 'amber';
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

function decisionText(value?: Decision | null) {
  if (!value?.decision) return 'Cannot tell';
  const name = value.decision === 'INSUFFICIENT_EVIDENCE' ? 'Cannot tell' : pretty(value.decision);
  return value.decision === 'TARGET' && value.strength != null ? `${name} · ${value.strength}` : name;
}

function reviewCandidate(event?: PairedEvent) {
  const review = event?.review;
  if (!review) return undefined;
  return review.overall?.candidate_id
    || review.image?.candidate_id
    || review.metadata?.candidate_id
    || review.notes?.candidate_id;
}

function draftForEvent(event?: PairedEvent, currentCandidate?: string): ReviewDraft {
  const storedCandidate = reviewCandidate(event);
  const review = !currentCandidate || storedCandidate === currentCandidate ? event?.review : undefined;
  return {
    image: review?.image?.decision || 'INSUFFICIENT_EVIDENCE',
    imageStrength: review?.image?.strength || 50,
    metadata: review?.metadata?.decision || 'INSUFFICIENT_EVIDENCE',
    metadataStrength: review?.metadata?.strength || 50,
    overall: review?.overall?.decision || 'INSUFFICIENT_EVIDENCE',
    notes: review?.notes?.text || '',
    excluded: { ...(review?.photo_exclusions?.excluded || {}) },
  };
}

function DecisionEditor({ label, decision, strength, disabled = false, onDecision, onStrength }: {
  label: string;
  decision: DecisionName;
  strength: number;
  disabled?: boolean;
  onDecision: (decision: DecisionName) => void;
  onStrength: (strength: number) => void;
}) {
  return <div className="decision-editor">
    <label>{label} decision
      <select value={decision} disabled={disabled} onChange={change => onDecision(change.target.value as DecisionName)}>
        <option value="TARGET">TARGET</option>
        <option value="NOT_TARGET">NOT TARGET</option>
        <option value="INSUFFICIENT_EVIDENCE">Cannot tell</option>
      </select>
    </label>
    {decision === 'TARGET' && <label>{label} target strength
      <input
        type="number"
        min="50"
        max="100"
        step="5"
        value={strength}
        onChange={change => onStrength(Number(change.target.value))}
      />
    </label>}
  </div>;
}

function PhotoEvidence({ event }: { event: PairedEvent }) {
  const probes = new Map((event.prediction?.photos || []).map(photo => [photo.sha256, photo]));
  const tags = new Map((event.photo_tags || []).map(photo => [photo.sha256, photo]));
  if (!event.evidence_id || !event.photo_manifest?.length) {
    return <div className="empty"><Images size={24}/><p>No stored photos for this listing.</p></div>;
  }
  return <div className="evidence-grid">
    {event.photo_manifest.map((photo, index) => {
      const probe = probes.get(photo.sha256);
      const tag = tags.get(photo.sha256);
      return <article className="evidence-card" key={photo.sha256}>
        <button className="photo-button" type="button" aria-label={`Open photo ${index + 1}`}>
          <img
            src={mediaUrl(event.evidence_id!, photo.sha256, true)}
            alt={`Saved listing photo ${index + 1}`}
            loading="lazy"
          />
        </button>
        <div className="photo-copy">
          <div className="photo-heading"><strong>Saved photo {index + 1}</strong><Badge>Immutable evidence</Badge></div>
          <p className="small-copy">Current probe · {probe?.room || 'room unknown'}</p>
          <p className="small-copy">{decisionText(probe)}</p>
          <p className="small-copy">Older silver tag · {tag?.room || 'room unknown'} · {decisionText(tag)}</p>
        </div>
      </article>;
    })}
  </div>;
}

function ProvenanceSummary({ property, event }: { property: PairedProperty; event?: PairedEvent }) {
  const candidateId = property.candidate?.candidate_id;
  const reviewedCandidate = reviewCandidate(event);
  const stale = Boolean(candidateId && reviewedCandidate && candidateId !== reviewedCandidate);
  return <section className="provenance-stack" aria-label="Evidence provenance">
    <div className="evidence-summary">
      <div>
        <span className="eyebrow">CURRENT MODEL</span>
        <strong>Current candidate: {candidateId || 'Not established'}</strong>
        <small>Current property and per-photo outputs</small>
      </div>
      <div>
        <span className="eyebrow">SAVED HUMAN REVIEW</span>
        <strong>Reviewed candidate: {reviewedCandidate || 'Not established'}</strong>
        <small>Evidence-bound and revisioned</small>
      </div>
      <div>
        <span className="eyebrow">LISTING EVIDENCE</span>
        <strong>{event?.evidence_id || 'Not established'}</strong>
        <small>Saved snapshot identity</small>
      </div>
    </div>
    {stale && <div className="notice warning">Earlier prediction review — not transferred</div>}
    <div className="model-grid">
      <article className="model-card">
        <span className="eyebrow">PROPERTY PHOTOS</span>
        <h3>Learned property image head</h3>
        <strong>{decisionText(event?.prediction?.image)}</strong>
        <p>Not a vote over photo probes. The learned head aggregates room-weighted image embeddings.</p>
      </article>
      <article className="model-card">
        <span className="eyebrow">CURRENT SYSTEM</span>
        <h3>Current per-photo model probes</h3>
        <p>Diagnostic affinity outputs from the current candidate. They are not human labels.</p>
      </article>
      <article className="model-card">
        <span className="eyebrow">SEPARATE OLDER SYSTEM</span>
        <h3>Older automated-silver tags</h3>
        <p>Frozen automated-silver references. They are not equivalent to current probes.</p>
      </article>
    </div>
  </section>;
}

export default function EvidenceReview() {
  const [items, setItems] = useState<PairedQueueItem[]>([]);
  const [property, setProperty] = useState<PairedProperty | null>(null);
  const [eventIndex, setEventIndex] = useState(0);
  const [search, setSearch] = useState('');
  const [tab, setTab] = useState<Tab>('evidence');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [draft, setDraft] = useState<ReviewDraft>(() => draftForEvent());

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return items;
    return items.filter(item =>
      `${item.address} ${item.group_id} ${item.prior_mls || ''} ${item.last_mls || ''}`.toLowerCase().includes(query));
  }, [items, search]);
  const selectedIndex = filtered.findIndex(item => item.group_id === property?.group_id);
  const event = property?.events[eventIndex];
  const hasUsablePhotos = Boolean(event?.photo_manifest?.some(photo => !draft.excluded[photo.sha256]));

  async function openProperty(groupId: string) {
    const detail = await loadProperty(groupId);
    setProperty(detail);
    setEventIndex(0);
    setDraft(draftForEvent(detail.events[0], detail.candidate?.candidate_id));
    setTab('evidence');
    history.replaceState(null, '', `/property-review?property=${encodeURIComponent(groupId)}`);
  }

  async function refresh(preferred?: string) {
    setBusy(true);
    setError('');
    try {
      await loadStatus();
      const queue = await loadQueue();
      setItems(queue.items || []);
      const requested = new URLSearchParams(window.location.search).get('property');
      const next = queue.items.find(item => item.group_id === preferred)
        || queue.items.find(item => item.group_id === requested)
        || queue.items.find(item => item.group_id === property?.group_id)
        || queue.items[0];
      if (next) await openProperty(next.group_id);
      else setProperty(null);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  function changeProperty(offset: number) {
    const item = filtered[selectedIndex + offset];
    if (item) void openProperty(item.group_id).catch(caught => setError((caught as Error).message));
  }

  function selectEvent(index: number) {
    setEventIndex(index);
    setDraft(draftForEvent(property?.events[index], property?.candidate?.candidate_id));
    setNotice('');
  }

  async function save(next: boolean) {
    if (!property || !event?.evidence_id) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await saveCorrection({
        evidence_id: event.evidence_id,
        expected_revision: event.revision || 0,
        image: {
          decision: hasUsablePhotos ? draft.image : 'INSUFFICIENT_EVIDENCE',
          strength: hasUsablePhotos && draft.image === 'TARGET' ? draft.imageStrength : null,
        },
        metadata: {
          decision: draft.metadata,
          strength: draft.metadata === 'TARGET' ? draft.metadataStrength : null,
        },
        overall: draft.overall,
        notes: draft.notes,
        candidate_id: property.candidate?.candidate_id,
        excluded_photos: draft.excluded,
      });
      setNotice('Review saved against this evidence and candidate.');
      const nextItem = next ? filtered[selectedIndex + 1] : undefined;
      if (nextItem) await openProperty(nextItem.group_id);
      else {
        const updated = await loadProperty(property.group_id);
        setProperty(updated);
        const updatedIndex = Math.min(eventIndex, Math.max(0, updated.events.length - 1));
        setEventIndex(updatedIndex);
        setDraft(draftForEvent(updated.events[updatedIndex], updated.candidate?.candidate_id));
      }
    } catch (caught) {
      const message = (caught as Error).message;
      if (message.includes('changed in another window') && event.evidence_id) {
        try {
          const updated = await loadProperty(property.group_id);
          const updatedIndex = Math.max(0, updated.events.findIndex(item => item.evidence_id === event.evidence_id));
          setProperty(updated);
          setEventIndex(updatedIndex);
          setError(`${message} The latest revision is loaded and your unsaved inputs were kept.`);
        } catch (reloadError) {
          setError(`${message} Reload failed: ${(reloadError as Error).message}`);
        }
      } else {
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  }

  return <div className="app-shell">
    <header className="topbar">
      <a className="brand" href="/property-review">
        <span className="brand-mark"><ShieldCheck size={18}/></span>
        <span><strong>ACQ Vision</strong><small>Paired condition review</small></span>
      </a>
      <div className="top-meta">
        <Badge tone="green">Private workspace</Badge>
        <button className="icon-button" type="button" onClick={() => void refresh(property?.group_id)} aria-label="Refresh queue">
          <RefreshCw size={16}/>
        </button>
      </div>
    </header>
    <div className="workspace">
      <aside className="queue-panel">
        <div className="queue-title"><div><span className="eyebrow">SAVED QUEUE</span><h1>Property reviews</h1></div><Badge>{items.length}</Badge></div>
        <label className="search-box"><Search size={16}/><input value={search} onChange={change => setSearch(change.target.value)} placeholder="Filter saved address or MLS"/></label>
        <p className="queue-help">This filters saved review records. It does not search or import from an MLS provider.</p>
        <div className="queue-list">
          {filtered.map(item => <button
            className={`queue-item ${item.group_id === property?.group_id ? 'active' : ''}`}
            type="button"
            key={item.group_id}
            onClick={() => void openProperty(item.group_id).catch(caught => setError((caught as Error).message))}
          >
            <span className="queue-address">{item.address}</span>
            <span className="queue-city">Prior {item.prior_mls || 'MLS missing'} · Last {item.last_mls || 'MLS missing'}</span>
            <span className="queue-bottom"><Badge tone={item.reviewed ? 'green' : 'amber'}>{item.reviewed ? 'Reviewed' : 'Needs review'}</Badge></span>
          </button>)}
        </div>
      </aside>
      <main className="review-main">
        {error && <div className="alert error" role="alert">{error}</div>}
        {notice && <div className="alert success" role="status">{notice}</div>}
        {busy && !property && <div className="empty">Loading saved review evidence…</div>}
        {!busy && !property && !error && <div className="empty">No saved properties match this queue.</div>}
        {property && <>
          <div className="property-header">
            <div><span className="eyebrow">SAVED PROPERTY</span><h2>{property.address}</h2><p>{property.apn ? `APN ${property.apn}` : 'APN not established'}</p></div>
            <div className="nav-buttons">
              <button className="icon-button" type="button" disabled={selectedIndex <= 0} onClick={() => changeProperty(-1)} aria-label="Previous property"><ChevronLeft size={18}/></button>
              <button className="icon-button" type="button" disabled={selectedIndex < 0 || selectedIndex >= filtered.length - 1} onClick={() => changeProperty(1)} aria-label="Next property"><ChevronRight size={18}/></button>
            </div>
          </div>
          <div className="event-switcher" aria-label="Listing evidence">
            {property.events.map((item, index) => <button
              type="button"
              key={`${item.event_role}-${item.evidence_id || index}`}
              className={index === eventIndex ? 'active' : ''}
              onClick={() => selectEvent(index)}
            >
              {item.event_role === 'acquisition' ? 'Prior sale' : item.event_role === 'resale' ? 'Resale' : pretty(item.event_role)}
              <small>{item.listing_id || 'MLS not established'}</small>
            </button>)}
          </div>
          <ProvenanceSummary property={property} event={event}/>
          <div className="tabs" role="tablist">
            {([['evidence', 'Saved evidence'], ['models', 'Model outputs'], ['review', 'Human review'], ['source', 'Listing source']] as const).map(([key, label]) =>
              <button type="button" role="tab" aria-selected={tab === key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)} key={key}>{label}</button>)}
          </div>
          {tab === 'evidence' && event && <section className="tab-content">
            <PhotoEvidence event={event}/>
          </section>}
          {tab === 'models' && event && <section className="tab-content model-grid">
            <article className="model-card"><h3>Photos</h3><strong>{decisionText(event.prediction?.image)}</strong></article>
            <article className="model-card"><h3>Metadata</h3><strong>{decisionText(event.prediction?.metadata)}</strong></article>
            <article className="model-card"><h3>Overall</h3><strong>{decisionText(event.prediction?.overall)}</strong></article>
          </section>}
          {tab === 'review' && event && <section className="tab-content review-layout">
            <div className="review-card"><h3>Independent condition review</h3>
              {!hasUsablePhotos && <div className="notice">No usable stored photos. Photos must remain Cannot tell.</div>}
              <div className="decision-fields">
                <DecisionEditor
                  label="Photos"
                  decision={hasUsablePhotos ? draft.image : 'INSUFFICIENT_EVIDENCE'}
                  strength={draft.imageStrength}
                  disabled={!hasUsablePhotos}
                  onDecision={image => setDraft(current => ({ ...current, image }))}
                  onStrength={imageStrength => setDraft(current => ({ ...current, imageStrength }))}
                />
                <DecisionEditor
                  label="Metadata"
                  decision={draft.metadata}
                  strength={draft.metadataStrength}
                  onDecision={metadata => setDraft(current => ({ ...current, metadata }))}
                  onStrength={metadataStrength => setDraft(current => ({ ...current, metadataStrength }))}
                />
                <label>Overall decision
                  <select value={draft.overall} onChange={change => setDraft(current => ({ ...current, overall: change.target.value as DecisionName }))}>
                    <option value="TARGET">TARGET</option>
                    <option value="NOT_TARGET">NOT TARGET</option>
                    <option value="INSUFFICIENT_EVIDENCE">Cannot tell</option>
                  </select>
                </label>
              </div>
              <label>Review notes
                <textarea
                  value={draft.notes}
                  maxLength={4000}
                  onChange={change => setDraft(current => ({ ...current, notes: change.target.value }))}
                  placeholder="Describe the specific evidence and whether it is localized or broad."
                />
              </label>
              <div className="review-note">These human decisions stay separate from model outputs and do not modify a frozen dataset.</div>
              <div className="form-actions">
                <button className="button secondary" type="button" disabled={busy} onClick={() => void save(false)}>Save review</button>
                <button className="button primary" type="button" disabled={busy} onClick={() => void save(true)}>Save and next</button>
              </div>
            </div>
          </section>}
          {tab === 'source' && event && <section className="tab-content"><pre>{JSON.stringify({
            listing: event.raw_snapshot,
            metadata_input: event.metadata_snapshot,
          }, null, 2)}</pre></section>}
        </>}
      </main>
    </div>
  </div>;
}
