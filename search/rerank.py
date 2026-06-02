from sentence_transformers import CrossEncoder


class Reranker:
    def __init__(self, model_name: str):
        self.model = CrossEncoder(model_name)

    def rank(self, query: str, documents):
        """Return (best_doc, best_score) for the single highest-scoring doc."""
        results = self.rank_top_k(query, documents, k=1)
        if not results:
            return None, None
        return results[0]

    def rank_top_k(self, query: str, documents, k: int = 1):
        """
        Return up to `k` (doc, score) pairs ordered by score (best first).
        Robust whether predict() returns a numpy array, torch tensor, or
        plain list.
        """
        if not documents or k < 1:
            return []

        pairs = [[query, doc] for doc in documents]
        scores = self.model.predict(pairs)

        # Normalize to plain Python floats so the rest doesn't have to care
        # whether the encoder returned numpy, torch, or a list.
        if hasattr(scores, "tolist"):
            score_list = [float(s) for s in scores.tolist()]
        else:
            score_list = [float(s) for s in scores]

        ranked_idx = sorted(
            range(len(score_list)),
            key=lambda i: score_list[i],
            reverse=True,
        )
        return [(documents[i], score_list[i]) for i in ranked_idx[:k]]