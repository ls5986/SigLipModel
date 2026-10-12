import pickle
import numpy as np
import pytest
from semantic_text import SemanticTextEncoder
from text_model import TextModel
from condition_schema import TEXT_SIGNALS
from v1_models import MetadataClassifier


class Tokenizer:
    def encode(self, text, **kwargs):
        return text.split()
    def decode(self, tokens):
        return ' '.join(tokens)


class Backend:
    tokenizer = Tokenizer()
    def get_sentence_embedding_dimension(self):
        return 2
    def encode(self, chunks, **kwargs):
        return np.asarray([[float('renovated' in text), float('original' in text)] for text in chunks])


def encoder():
    result = SemanticTextEncoder('/not-provisioned', '0' * 64, chunk_tokens=16)
    result._model = Backend()
    return result


def test_long_remarks_include_tail_and_missing_is_zero():
    model = encoder()
    matrix = model.transform(['original ' + 'home ' * 16 + 'renovated', '']).toarray()
    assert matrix[0, 0] > 0 and matrix[0, 1] > 0
    assert (matrix[1] == 0).all()


def test_bundle_does_not_pickle_encoder_runtime():
    restored = pickle.loads(pickle.dumps(encoder()))
    assert restored._model is None
    with pytest.raises(ValueError, match='not provisioned'):
        restored.transform(['original kitchen'])


def test_changed_checkpoint_fails_before_loading_dependency(tmp_path):
    (tmp_path / 'config.json').write_text('{}')
    with pytest.raises(ValueError, match='hash changed'):
        SemanticTextEncoder(tmp_path, '0' * 64).transform(['home'])


def test_semantic_heads_keep_unknown_unassessed():
    tag = TEXT_SIGNALS[0]
    labels = [{'text_signals': {tag: state}} for state in ['PRESENT', 'ABSENT', 'UNKNOWN']]
    model = TextModel.fit(['original kitchen', 'renovated kitchen', 'home'], labels, encoder=encoder())
    assert model.feature_schema_version == 'actvision-text-semantic-v1'
    assert len(model.signals[tag].classes_) == 2
    assert model.predict([''])[0]['text_signals'] == []
    with pytest.raises(ValueError, match='positive AND negative'):
        TextModel.fit(['original kitchen'], [{'text_signals': {tag:'PRESENT'}}], encoder=encoder())


def test_metadata_classifier_accepts_contextual_features_without_tfidf():
    model = MetadataClassifier.fit([{}] * 8,
        ['original kitchen'] * 4 + ['renovated kitchen'] * 4,
        np.asarray([1] * 4 + [0] * 4), text_encoder=encoder())
    assert model.text.feature_schema_version == 'actvision-text-semantic-v1'
    assert model.predict([{}], ['original kitchen'])[0] > model.predict([{}], ['renovated kitchen'])[0]


def test_non_text_is_rejected():
    with pytest.raises(ValueError, match='descriptions must be strings'):
        encoder().transform([{'YearBuilt': 2026}])


def test_semantic_encoder_reaches_oof_and_final_fusion_bundle():
    from test_v1_models import synthetic
    from workbench_training import train_candidate
    metadata, remarks, bags, y, groups = synthetic()
    rows = [{'property_id':str(i), 'group_id':groups[i],
             'split':'train' if i<10 else 'validation' if i<14 else 'test',
             'target_label':'TARGET' if i%2 else 'NOT_TARGET',
             'metadata':metadata[i], 'remarks':remarks[i], 'vectors':bags[i],
             'image_count':2} for i in range(16)]
    bundle = train_candidate(rows, text_encoder=encoder())
    assert bundle['metadata_model'].text.feature_schema_version == 'actvision-text-semantic-v1'
    assert bundle['metrics']['text_features']['checkpoint_sha256'] == '0'*64
    assert bundle['metrics']['fusion_training']['protected_test_used'] is False
