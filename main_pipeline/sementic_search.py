import json
from sentence_transformers import SentenceTransformer, CrossEncoder
from chromadb import PersistentClient


# ------------------------------
# READ QUERIES
# ------------------------------
def read_queries(filename):
    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [item["tool_description"] for item in data]


# ------------------------------
# SEMANTIC SEARCH
# ------------------------------
def semantic_search(query, persist_directory, encoder_model, top_k=10):
    model = SentenceTransformer(encoder_model)
    query_emb = model.encode([query])[0]

    client = PersistentClient(path=persist_directory)
    collection = client.get_or_create_collection("tools")

    result = collection.query(
        query_embeddings=[query_emb.tolist()],
        n_results=top_k,
        include=["documents"]
    )

    docs = result["documents"][0] if result["documents"] else []
    return docs


# ------------------------------
# CROSS-ENCODER RERANK
# ------------------------------
def rerank(query, docs, cross_encoder_model):
    cross_encoder = CrossEncoder(cross_encoder_model)
    pairs = [[query, doc] for doc in docs]
    scores = cross_encoder.predict(pairs)

    top_idx = int(scores.argmax()) if hasattr(scores, 'argmax') else scores.index(max(scores))
    return docs[top_idx], float(scores[top_idx])


# ------------------------------
# PARSE TOON-STYLE CHUNK TEXT
# ------------------------------
def parse_chunk_text(chunk_str):
    result = {}

    for line in chunk_str.split('\n'):
        if line.startswith("ID:"):
            result["tool_id"] = line.replace("ID:", "").strip()
        elif line.startswith("Name:"):
            result["tool_name"] = line.replace("Name:", "").strip()
        elif line.startswith("Description:"):
            result["tool_description"] = line.replace("Description:", "").strip()

    start = chunk_str.find("https")
    end = chunk_str.find(".git", start)
    if start != -1 and end != -1:
        result["tool_git_link"] = chunk_str[start:end+4].strip()

    return result


# ------------------------------
# MAIN SEARCH PROCESS (REUSABLE)
# ------------------------------
def run_query_batch(
    queries,
    persist_dir,
    encoder_model,
    cross_encoder_model,
    top_k=10
):
    results = []

    for query in queries:
        docs = semantic_search(query, persist_dir, encoder_model, top_k)

        if not docs:
            item = {"query": query, "score": None}
        else:
            match, score = rerank(query, docs, cross_encoder_model)
            fields = parse_chunk_text(match)
            item = {"query": query, "score": score, **fields}

        results.append(item)

    return results
