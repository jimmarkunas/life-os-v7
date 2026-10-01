"""Optional semantic layer (shadow mode): a small local sentence-embedding model reads paraphrase, nothing else.

It cannot invent anything: it only ranks how close a requirement line is to the phrases of the private profile's own
capabilities, and the CLASS always comes from that capability (never from the model). Below LOW it is no match
(fail closed: the requirement stays Unsupported); between LOW and HIGH it is one evidence step weaker than the capability.
The model runs locally (no text leaves the runner); any failure to load it turns the layer off and scoring is unchanged.
"""
import os

HIGH = 0.82
LOW = 0.72
STEP_DOWN = {"direct": "adjacent", "adjacent": "method", "method": "unsupported"}
MODEL = "BAAI/bge-small-en-v1.5"


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


class Matcher:
    """embed: list[str] -> list[vector] (unit-length vectors, so a dot product is the cosine)."""

    def __init__(self, profile, embed):
        self.embed, self.phrases, self.rows = embed, [], []
        for row in [*profile.capabilities, *profile.functions]:
            for text in dict.fromkeys([*row.get("terms", ()), row["label"]]):
                if len(text) > 3:                    # one- and two-letter terms say nothing in a sentence
                    self.phrases.append(text)
                    self.rows.append(row)
        self.vectors = embed(self.phrases) if self.phrases else []
        self._np = None
        try:
            import numpy                             # noqa: PLC0415 - optional speed-up, present with the model runtime
            self._np = numpy
            self.vectors = numpy.asarray(self.vectors, dtype="float32")
        except ImportError:
            pass

    def best_many(self, texts):
        """[(row, similarity, class)] per text; row is None below LOW."""
        if not texts or not self.phrases:
            return [(None, 0.0, "unsupported")] * len(texts)
        vecs = self.embed(list(texts))
        out = []
        for vec in vecs:
            if self._np is not None:
                sims = self.vectors @ self._np.asarray(vec, dtype="float32")
                index = int(sims.argmax())
                sim = float(sims[index])
            else:
                sims = [_dot(v, vec) for v in self.vectors]
                index = max(range(len(sims)), key=sims.__getitem__)
                sim = sims[index]
            row = self.rows[index]
            if sim < LOW:
                out.append((None, sim, "unsupported"))
            elif sim >= HIGH:
                out.append((row, sim, row["class"]))
            else:
                out.append((row, sim, STEP_DOWN.get(row["class"], "unsupported")))
        return out


def load_embedder(environ=os.environ):
    """The local embedding function, or None when the model cannot be loaded (layer off, scoring unchanged)."""
    try:
        from fastembed import TextEmbedding              # noqa: PLC0415 - optional dependency (requirements-fit.txt)
        model = TextEmbedding(MODEL, cache_dir=environ.get("FASTEMBED_CACHE") or None)
    except Exception:                                     # noqa: BLE001 - any failure means "off", never a crash
        return None
    return lambda texts: [list(map(float, v)) for v in model.embed(list(texts))]
