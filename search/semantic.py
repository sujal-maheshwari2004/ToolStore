from sentence_transformers import SentenceTransformer
from chromadb import PersistentClient
from .rerank import Reranker


class SemanticSearcher:
    def __init__(self, persist_dir, encoder_model, cross_encoder_model, top_k=10):
        self.encoder = SentenceTransformer(encoder_model)
        self.client = PersistentClient(path=str(persist_dir))
        self.collection = self.client.get_or_create_collection("tools")
        self.reranker = Reranker(cross_encoder_model)
        self.top_k = top_k

    def batch_search(self, queries):
        return [self.search(q) for q in queries]

    def search(self, query):
        # 1️⃣ Embed
        embedding = self.encoder.encode([query])[0]

        # 2️⃣ Retrieve top-k
        results = self.collection.query(
            query_embeddings=[embedding.tolist()],
            n_results=self.top_k,
            include=["documents"],
        )

        docs = results["documents"][0] if results["documents"] else []

        # 3️⃣ Rerank
        best_doc, best_score = self.reranker.rank(query, docs)

        if not best_doc:
            return {
                "query": query,
                "tool_id": None,
                "tool_name": None,
                "tool_description": None,
                "tool_git_link": None,
                "score": None,
            }

        # 4️⃣ Parse structured fields
        parsed = self._parse_chunk(best_doc)

        return {
            "query": query,
            **parsed,
            "score": best_score,
        }

    def _parse_chunk(self, chunk_text):
        result = {
            "tool_id": None,
            "tool_name": None,
            "tool_description": None,
            "tool_git_link": None,
        }

        for line in chunk_text.split("\n"):
            if line.startswith("ID:"):
                result["tool_id"] = line.replace("ID:", "").strip()
            elif line.startswith("Name:"):
                result["tool_name"] = line.replace("Name:", "").strip()
            elif line.startswith("Description:"):
                result["tool_description"] = line.replace("Description:", "").strip()
            elif line.startswith("Git Link:"):
                result["tool_git_link"] = line.replace("Git Link:", "").strip()

        return result
