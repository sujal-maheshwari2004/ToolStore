from typing import List, Optional, Dict
from sentence_transformers import SentenceTransformer
from chromadb import PersistentClient
from .rerank import Reranker


class SemanticSearcher:
    def __init__(
        self,
        persist_dir,
        encoder_model,
        cross_encoder_model,
        top_k: int = 10,
    ):
        self.encoder    = SentenceTransformer(encoder_model)
        self.client     = PersistentClient(path=str(persist_dir))
        self.collection = self.client.get_or_create_collection("tools")
        self.reranker   = Reranker(cross_encoder_model)
        # Pool size pulled from ChromaDB before reranking.
        self.top_k      = top_k

    # ==================================================
    # PUBLIC API
    # ==================================================

    def batch_search(
        self,
        queries: List[str],
        dedupe: bool = False,
    ) -> List[Dict]:
        """
        Run search() over a list of queries. Returns one result dict per
        query in the same order.

        With dedupe=True, queries that resolve to a tool already returned
        earlier in the batch get an empty result with `duplicate_of` set,
        so callers can correlate by position without re-cloning the same
        repo for multiple queries.
        """
        results: List[Dict] = []
        seen_ids = set()
        for q in queries:
            r = self.search(q)
            tool_id = r.get("tool_id")
            if dedupe and tool_id and tool_id in seen_ids:
                empty = self._empty_result(q)
                empty["duplicate_of"] = tool_id
                results.append(empty)
                continue
            if tool_id:
                seen_ids.add(tool_id)
            results.append(r)
        return results

    def search(self, query: str) -> Dict:
        """Return the single best tool match for `query`, reranked."""
        results = self.search_top_k(query, k=1)
        if not results:
            return self._empty_result(query)
        return results[0]

    def search_top_k(self, query: str, k: int = 1) -> List[Dict]:
        """
        Return up to `k` tool matches for `query`, reranked, best first.
        Returns [] if nothing matches.
        """
        if k < 1:
            return []

        embedding = self.encoder.encode([query])[0]
        # Encoder may return numpy / torch tensor / list; normalize.
        if hasattr(embedding, "tolist"):
            embedding_list = embedding.tolist()
        else:
            embedding_list = list(embedding)

        retrieved = self.collection.query(
            query_embeddings=[embedding_list],
            n_results=self.top_k,
            include=["documents"],
        )

        docs = retrieved["documents"][0] if retrieved.get("documents") else []
        if not docs:
            return []

        scored = self.reranker.rank_top_k(query, docs, k=k)

        results: List[Dict] = []
        for doc, score in scored:
            parsed = self._parse_chunk(doc)
            results.append({"query": query, **parsed, "score": score})
        return results

    # ==================================================
    # INTERNAL
    # ==================================================

    @staticmethod
    def _empty_result(query: str) -> Dict:
        return {
            "query":            query,
            "tool_id":          None,
            "tool_name":        None,
            "tool_description": None,
            "tool_git_link":    None,
            "score":            None,
        }

    def _parse_chunk(self, chunk_text: str) -> Dict:
        """
        Parse a chunk into structured fields. Each field is taken from its
        FIRST occurrence; later lines that happen to start with a prefix
        (e.g. "Name:" appearing inside a Description) do not overwrite the
        real field.
        """
        result = {
            "tool_id":          None,
            "tool_name":        None,
            "tool_description": None,
            "tool_git_link":    None,
        }

        prefixes = (
            ("ID:",          "tool_id"),
            ("Name:",        "tool_name"),
            ("Description:", "tool_description"),
            ("Git Link:",    "tool_git_link"),
        )

        for line in chunk_text.split("\n"):
            for prefix, key in prefixes:
                if result[key] is None and line.startswith(prefix):
                    result[key] = line[len(prefix):].strip()
                    break

        return result