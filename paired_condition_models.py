"""Binary dated/updated research heads. Never invent the older C1-C6 labels.

Only train split fits vocabularies, scales and heads. Fusion uses grouped OOF
component predictions. All outputs are uncalibrated affinities, not deal odds.
"""
from collections import Counter, defaultdict
import hashlib
import json
import math
import re

import numpy as np
from scipy.special import expit
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, f1_score, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

POLICY = 'paired-condition-heads-v1'
KNOWN = {'TARGET', 'NOT_TARGET'}
COMPONENTS = ('vision', 'text', 'structured', 'fusion')
NUMERIC = ('YearBuilt', 'LivingArea', 'BedroomsTotal', 'BathroomsTotalInteger',
           'LotSizeSquareFeet', 'AssociationFee')
CATEGORICAL = ('PropertyType', 'PropertySubType', 'PropertyAttachedYN',
               'AssociationYN', 'Appliances', 'Flooring', 'Heating', 'Cooling',
               'Roof', 'ConstructionMaterials', 'Sewer', 'PropertyCondition')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True).encode()).hexdigest()


def sanitize_text(public, private):
    def scrub(text):
        text = re.sub(r'https?://\S+|\b\S+@\S+\b', ' ', str(text or ''))
        text = re.sub(r'(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)', ' ', text)
        text = re.sub(r'(?i)\b(?:gate|alarm|lockbox|supra)\s*(?:code|combination)?\s*[:#]?\s*\S+', ' ', text)
        text = re.sub(r'(?i)mandatory remarks none known|optional bedroom equipment|(?:\w+\s+)?(?:bed dimension|room dim)[^.;\n]*', ' ', text)
        return ' '.join(text.split())
    # Private remarks enter only as relevant condition/legal/finance sentences.
    # Showing contacts, access instructions and flattened dimensions stay out.
    relevant = r'(?i)\b(?:dated|original|updat\w*|remodel\w*|renovat\w*|repair\w*|fixer|TLC|as[ -]is|cash|probate|trust|permit\w*|foundation|termite|mold|fire damage|water damage)\b'
    private_parts = [scrub(s) for s in re.split(r'[.;\n]+', str(private or ''))
                     if re.search(relevant, s) and not re.search(r'(?i)gate code|alarm code|lockbox code', s)]
    return (scrub(public) + '\n' + '\n'.join(private_parts)).strip()[:32000]


def structured_features(meta):
    result = {}
    for key in NUMERIC:
        value = meta.get(key)
        try:
            number = float(value) if value not in (None, '') and not isinstance(value, bool) else None
        except (TypeError, ValueError):
            number = None
        if number is not None and not math.isfinite(number):
            number = None
        result[key + '_missing'] = float(number is None)
        if number is not None:
            result[key] = number
    for key in CATEGORICAL:
        value = meta.get(key)
        values = value if isinstance(value, list) else [value]
        values = [str(v).strip().casefold()[:100] for v in values if v not in (None, '')]
        result[key + '_missing'] = float(not values)
        for value in sorted(set(values)):
            result[key + '=' + value] = 1.
    # No prices/DOM from unverified closed snapshots, remarks, IDs, event roles,
    # outcome values, geography or workbook target flags enter this condition head.
    return result


def vision_features(photos):
    photos = [p for p in photos if p.get('exclusion') == 'none' and p.get('image_embedding')]
    if not photos:
        return None
    vectors = np.asarray([p['image_embedding'] for p in photos], dtype=float)
    if vectors.ndim != 2 or not np.isfinite(vectors).all():
        raise ValueError('Invalid vision embeddings')
    weights = np.asarray([2. if p.get('room') in {'kitchen', 'bathroom'} else 1. for p in photos])
    # The cached embeddings and their rooms are inputs; zero-shot target logits,
    # decisions, strength and staging margins never become condition features.
    return np.concatenate([np.average(vectors, axis=0, weights=weights), vectors.max(axis=0)]).tolist()


def guarded_splits(rows):
    """Protect linked MLS/interior-photo aliases without modifying the frozen set."""
    parent = {r['group_id']: r['group_id'] for r in rows}
    def find(g):
        while parent[g] != g:
            parent[g] = parent[parent[g]]
            g = parent[g]
        return g
    aliases = {}
    for row in rows:
        keys = [('listing', row.get('listing_key'))]
        keys += [('photo', p['sha256']) for p in row.get('photos', []) if p.get('exclusion') == 'none']
        for kind, key in keys:
            if not key:
                continue
            token = (kind, key)
            if token in aliases:
                parent[find(row['group_id'])] = find(aliases[token])
            aliases[token] = row['group_id']
    rank = {'train': 0, 'validation': 1, 'test': 2}
    protected = {}
    for row in rows:
        root = find(row['group_id'])
        protected[root] = max(rank[row['split']], protected.get(root, -1))
    inverse = {v: k for k, v in rank.items()}
    return [{**r, 'fit_group': find(r['group_id']), 'effective_split': inverse[protected[find(r['group_id'])]]} for r in rows]


def encode_structured(rows, spec=None):
    features = [structured_features(r.get('structured') or {}) for r in rows]
    if spec is None:
        vectorizer = DictVectorizer(sparse=False)
        matrix = vectorizer.fit_transform(features)
        names = vectorizer.get_feature_names_out().tolist()
        spec = {'names': names}
    else:
        names = spec['names']
        vocabulary = {name: i for i, name in enumerate(names)}
        matrix = np.zeros((len(rows), len(names)))
        for i, values in enumerate(features):
            for key, value in values.items():
                if key in vocabulary:
                    matrix[i, vocabulary[key]] = value
    return matrix, spec


def component_matrix(name, rows, spec=None):
    if name == 'structured':
        return encode_structured(rows, spec)
    key = 'vision_vector' if name == 'vision' else 'text_vector'
    dimension = spec['dimension'] if spec else len(next(r[key] for r in rows if r.get(key)))
    return np.asarray([r.get(key) or [0.] * dimension for r in rows]), {'dimension': dimension}


def has_input(row, name):
    return bool(row.get({'vision': 'vision_vector', 'text': 'text_vector', 'structured': 'structured'}[name]))


def fit_head(matrix, labels, groups, spec):
    counts = {label: len({g for g, y in zip(groups, labels) if y == label}) for label in KNOWN}
    if min(counts.values()) < 3:
        return None
    group_counts = Counter(groups)
    weights = np.asarray([1. / group_counts[g] for g in groups])
    scaler = StandardScaler()
    values = scaler.fit_transform(matrix, sample_weight=weights)
    model = LogisticRegression(C=.5, max_iter=2000, class_weight='balanced', random_state=20261008)
    model.fit(values, labels, sample_weight=weights)
    return {'spec': spec, 'classes': model.classes_.tolist(), 'coef': model.coef_.tolist(),
            'intercept': model.intercept_.tolist(), 'mean': scaler.mean_.tolist(),
            'scale': scaler.scale_.tolist(), 'class_groups': counts, 'uncalibrated': True}


def fit_component(name, rows):
    axis = 'image' if name == 'vision' else 'metadata'
    selected = [r for r in rows if r['labels'].get(axis) in KNOWN and has_input(r, name)]
    if not selected:
        return None
    matrix, spec = component_matrix(name, selected)
    return fit_head(matrix, [r['labels'][axis] for r in selected], [r['fit_group'] for r in selected], spec)


def probability(head, matrix):
    values = (matrix - np.asarray(head['mean'])) / np.asarray(head['scale'])
    scores = expit((values @ np.asarray(head['coef']).T + np.asarray(head['intercept']))[:, 0])
    return scores if head['classes'][1] == 'TARGET' else 1. - scores


def predict_component(name, head, rows):
    if not head:
        return [None] * len(rows)
    matrix, _ = component_matrix(name, rows, head['spec'])
    probabilities = probability(head, matrix)
    return [float(p) if has_input(row, name) else None for p, row in zip(probabilities, rows)]


def fusion_matrix(rows, scores):
    values = []
    for i, row in enumerate(rows):
        value = []
        for name in ('vision', 'text', 'structured'):
            p = scores[name][i]
            value.extend([.5 if p is None else p, float(p is not None)])
        value.extend([min(len(row.get('photos', [])), 50) / 50., float(bool(row.get('text_vector')))])
        values.append(value)
    return np.asarray(values)


def classify(p):
    if p is None or .4 < p < .6:
        return {'decision': 'INSUFFICIENT_EVIDENCE', 'strength': None, 'affinity': p}
    return {'decision': 'TARGET' if p >= .6 else 'NOT_TARGET',
            'strength': max(50, min(100, round(100 * p))) if p >= .6 else None, 'affinity': p}


def combine(axes):
    present = [x for x in axes if x['affinity'] is not None]
    targets = [x for x in present if x['decision'] == 'TARGET']
    if targets:
        return {**max(targets, key=lambda x: x['affinity']), 'decision': 'TARGET'}
    if present and all(x['decision'] == 'NOT_TARGET' for x in present):
        return classify(max(x['affinity'] for x in present))
    return {'decision': 'INSUFFICIENT_EVIDENCE', 'strength': None, 'affinity': None}


def predict(bundle, rows):
    scores = {name: predict_component(name, bundle['heads'].get(name), rows)
              for name in ('vision', 'text', 'structured')}
    fusion = bundle['heads'].get('fusion')
    fused = probability(fusion, fusion_matrix(rows, scores)).tolist() if fusion else [None] * len(rows)
    results = []
    for i in range(len(rows)):
        image = classify(scores['vision'][i])
        text = classify(scores['text'][i])
        structured = classify(scores['structured'][i])
        metadata = combine([text, structured])
        # A metadata TARGET is allowed even when no photo is a TARGET.
        overall = combine([image, metadata])
        if image['decision'] != 'TARGET' and metadata['decision'] != 'TARGET' and (
                image['decision'] == 'INSUFFICIENT_EVIDENCE' or metadata['decision'] == 'INSUFFICIENT_EVIDENCE'):
            overall = {'decision': 'INSUFFICIENT_EVIDENCE', 'strength': None, 'affinity': None}
        if overall['decision'] == 'TARGET' and fused[i] is not None:
            # The learned late-fusion head ranks TARGET evidence on the requested
            # 50-100 scale; an image-only or metadata-only TARGET still qualifies.
            overall['strength'] = round(50 + 50 * fused[i])
        results.append({'image': image, 'text': text, 'structured': structured, 'metadata': metadata,
                        'overall': overall, 'fusion_affinity': fused[i], 'calibrated': False,
                        'disagreement': image['decision'] in KNOWN and metadata['decision'] in KNOWN and image['decision'] != metadata['decision']})
    return results


def metrics(rows, results, axis):
    pairs = [(r['labels'].get(axis), pred[axis]) for r, pred in zip(rows, results) if r['labels'].get(axis) in KNOWN]
    assessed = [(truth, pred['decision']) for truth, pred in pairs if pred['decision'] in KNOWN]
    output = {'known_labels': len(pairs), 'assessed': len(assessed), 'abstained': len(pairs) - len(assessed),
              'meaning': 'Agreement with automated silver labels; not independent human accuracy'}
    if assessed:
        truth, guesses = zip(*assessed)
        output.update(agreement=float(np.mean(np.asarray(truth) == np.asarray(guesses))),
                      macro_f1=float(f1_score(truth, guesses, average='macro', zero_division=0)))
        if len(set(truth)) == 2:
            output['balanced_accuracy'] = float(balanced_accuracy_score(truth, guesses))
    return output


def train(rows, progress=lambda stage: None):
    rows = guarded_splits(rows)
    selected = [r for r in rows if r['eligible']]
    fitting = [r for r in selected if r['effective_split'] == 'train']
    if len({r['fit_group'] for r in fitting}) < 20:
        raise ValueError('Need at least 20 independent training groups')
    oof = {name: [None] * len(fitting) for name in ('vision', 'text', 'structured')}
    groups = [r['fit_group'] for r in fitting]
    folds = GroupKFold(n_splits=min(5, len(set(groups))))
    for fold, (fit_indices, held_indices) in enumerate(folds.split(fitting, groups=groups), 1):
        progress('grouped_oof_' + str(fold))
        fit_rows = [fitting[i] for i in fit_indices]
        held_rows = [fitting[i] for i in held_indices]
        for name in oof:
            head = fit_component(name, fit_rows)
            for index, p in zip(held_indices, predict_component(name, head, held_rows)):
                oof[name][int(index)] = p
    progress('fit_final_heads')
    heads = {name: fit_component(name, fitting) for name in oof}
    photo_rows = [(r, p) for r in fitting for p in r.get('photos', [])
                  if p.get('exclusion') == 'none' and p.get('decision') in KNOWN and p.get('image_embedding')]
    photo_head = fit_head(np.asarray([p['image_embedding'] for _, p in photo_rows]),
        [p['decision'] for _, p in photo_rows], [r['fit_group'] for r, _ in photo_rows],
        {'dimension': len(photo_rows[0][1]['image_embedding'])}) if photo_rows else None
    known_indices = [i for i, r in enumerate(fitting) if r['labels'].get('overall') in KNOWN
                     and any(oof[name][i] is not None for name in oof)]
    heads['fusion'] = fit_head(fusion_matrix(fitting, oof)[known_indices],
        [fitting[i]['labels']['overall'] for i in known_indices],
        [groups[i] for i in known_indices], {'dimension': 8}) if known_indices else None
    if not any(heads.values()):
        raise ValueError('No modality has both known classes in three independent groups')
    bundle = {'policy': POLICY, 'heads': heads, 'training_rows': len(fitting),
              'photo_head': photo_head,
              'training_groups': len(set(groups)), 'production_ready': False,
              'label_scope': 'dated versus fully updated condition; not acquisition profitability',
              'calibrated': False, 'evaluation': {}, 'input_policy': 'paired-condition-inputs-v1'}
    for split in ('validation', 'test'):
        progress('evaluate_' + split)
        held = [r for r in selected if r['effective_split'] == split]
        results = predict(bundle, held) if held else []
        bundle['evaluation'][split] = {axis: metrics(held, results, axis) for axis in ('image', 'metadata', 'overall')}
        bundle['evaluation'][split]['groups'] = len({r['fit_group'] for r in held})
    bundle['predictions'] = {r['evidence_id']: pred for r, pred in zip(rows, predict(bundle, rows))}
    bundle['counts'] = dict(Counter(r['effective_split'] for r in selected))
    bundle['alias_promotions'] = sum(r['split'] != r['effective_split'] for r in rows)
    bundle['excluded_rows'] = len(rows) - len(selected)
    return bundle
