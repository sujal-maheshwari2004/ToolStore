from sentence_transformers import SentenceTransformer
from chromadb import PersistentClient
import re

TOON_FILE = "tools.toon"
CHROMA_DIR = "../toon_chroma_db"

import re

def parse_toon_table(filename):
    with open(filename, "r", encoding="utf-8") as f:
        lines = f.readlines()
    header = lines[0].strip()
    fields = header.split("{")[1].split("}")[0].split(",")
    tools = []
    for line in lines[1:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("  "):
            line = line[2:]
        # Split only on commas NOT preceded by a backslash
        values = re.split(r'(?<!\\),', line)
        # Remove escape characters for commas and newlines
        values = [v.replace('\\,', ',').replace('\\n', '\n') for v in values]
        entry = dict(zip([f.strip() for f in fields], values))
        tools.append(entry)
    return tools


def chunk_tools(tools):
    chunks = []
    for t in tools:
        chunk = (
            f"ID: {t['tool_id']}\n"
            f"Name: {t['tool_name']}\n"
            f"Description: {t['tool_description']}\n"
            f"Git Link: {t['tool_git_link']}"
        )
        chunks.append(chunk)
    return chunks

def embed_chunks_local(chunks, model_name="all-MiniLM-L6-v2"):
    model = SentenceTransformer(model_name)
    embeddings = model.encode(chunks, show_progress_bar=True)
    return embeddings

def store_in_chroma(chunks, embeddings, persist_directory):
    client = PersistentClient(path=persist_directory)
    collection = client.get_or_create_collection("tools")
    ids = [str(i) for i in range(len(chunks))]
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        collection.upsert(
            ids=[ids[i]],
            embeddings=[emb.tolist()],
            documents=[chunk]
        )

def main():
    tools = parse_toon_table(TOON_FILE)
    chunks = chunk_tools(tools)
    print(f"Found {len(chunks)} entries.")
    print("Embedding locally via sentence-transformers...")
    embeddings = embed_chunks_local(chunks)
    print("Storing in Chroma...")
    store_in_chroma(chunks, embeddings, CHROMA_DIR)
    print("Stored embeddings in Chroma at", CHROMA_DIR)

if __name__ == "__main__":
    main()
