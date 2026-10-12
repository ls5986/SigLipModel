"""Offline, frozen semantic features for supervised listing-description heads."""
from pathlib import Path
import hashlib
import numpy as np
from scipy.sparse import csr_matrix

FEATURE_SCHEMA_VERSION = 'actvision-text-semantic-v1'


class SemanticTextEncoder:
    """Load an explicitly provisioned checkpoint; never download or call APIs.

    Sentence embeddings are inputs to supervised heads, not similarity-to-target
    scores. Chunking preserves the end of long remarks instead of truncating it.
    """
    feature_schema_version = FEATURE_SCHEMA_VERSION

    def __init__(self, model_directory, checkpoint_sha256, *, chunk_tokens=192):
        self.model_directory = str(Path(model_directory).resolve())
        self.checkpoint_sha256 = checkpoint_sha256
        self.chunk_tokens = chunk_tokens
        self._model = None
        self.dimension = None
        if chunk_tokens < 16:
            raise ValueError('Semantic chunk size must be at least 16 tokens')

    def _load(self):
        if self._model is not None:
            return self._model
        root = Path(self.model_directory)
        if not root.is_dir():
            raise ValueError('Semantic encoder checkpoint is not provisioned')
        files = sorted(p for p in root.rglob('*') if p.is_file())
        if not files:
            raise ValueError('Semantic encoder checkpoint is empty')
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b'\0')
            with path.open('rb') as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(block)
        if digest.hexdigest() != self.checkpoint_sha256:
            raise ValueError('Semantic encoder checkpoint hash changed')
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(self.model_directory, device='cpu',
                                    local_files_only=True, trust_remote_code=False)
        model.eval()
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        if self.chunk_tokens + 2 > model.max_seq_length:
            raise ValueError('Semantic chunk exceeds encoder token limit')
        self._model = model
        return model

    def fit_transform(self, texts):
        return self.transform(texts)

    def transform(self, texts):
        if any(not isinstance(text, str) or len(text) > 50000 for text in texts):
            raise ValueError('Listing descriptions must be strings up to 50000 characters')
        model = self._load()
        self.dimension = model.get_sentence_embedding_dimension()
        output = []
        for text in texts:
            tokens = model.tokenizer.encode(text, add_special_tokens=False)
            if not tokens:
                output.append(np.zeros(self.dimension))
                continue
            chunks = [model.tokenizer.decode(tokens[i:i + self.chunk_tokens])
                      for i in range(0, len(tokens), self.chunk_tokens)]
            vectors = np.asarray(model.encode(chunks, batch_size=8,
                normalize_embeddings=True, convert_to_numpy=True,
                show_progress_bar=False), dtype=float)
            if vectors.shape != (len(chunks), self.dimension) or not np.isfinite(vectors).all():
                raise ValueError('Invalid semantic encoder output')
            # Length weighting avoids giving a short final chunk equal influence.
            weights = [min(self.chunk_tokens, len(tokens) - i)
                       for i in range(0, len(tokens), self.chunk_tokens)]
            output.append(np.average(vectors, axis=0, weights=weights))
        return csr_matrix(np.asarray(output).reshape(len(texts), self.dimension))

    def get_feature_names_out(self):
        if self.dimension is None:
            raise ValueError('Semantic encoder has not encoded descriptions')
        return np.asarray([f'semantic_dimension_{i}' for i in range(self.dimension)])

    def __getstate__(self):
        # Bundles retain checkpoint identity, never an implicit in-memory model.
        return {**self.__dict__, '_model': None}
