from sentence_transformers import CrossEncoder

class Reranker:
    def __init__(self, model_name: str):
        self.model = CrossEncoder(model_name)

    def rank(self, query: str, documents: list[str]):
        if not documents:
            return None, None

        pairs = [[query, doc] for doc in documents]
        scores = self.model.predict(pairs)

        best_index = int(scores.argmax())
        return documents[best_index], float(scores[best_index])
