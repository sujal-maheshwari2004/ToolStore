import os
from langchain.embeddings import OpenAIEmbeddings

TOON_FILE = "tools.toon"

def parse_toon_table(filename):
    """Parse a TOON table and return a list of dicts for each row."""
    with open(filename, "r", encoding="utf-8") as f:
        lines = f.readlines()

    header = lines[0].strip()
    # Parse fields from header
    # Example: tools[3]{tool_id,tool_name,tool_description,tool_git_link}:
    fields_part = header.split("{")[1].split("}")[0]
    fields = [f.strip() for f in fields_part.split(",")]

    tools = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):  # Skip comment lines, if any
            continue
        # Remove leading indentation
        if line.startswith("  "):
            line = line[2:]
        values = [v.replace("\\,", ",").replace("\\n", "\n") for v in line.split(",")]
        entry = dict(zip(fields, values))
        tools.append(entry)
    return tools

def chunk_tools(tools):
    """Return a list of strings, one for each tool, formatted for embedding."""
    chunks = []
    for t in tools:
        # Consolidate info into one string per entry
        chunk = f"ID: {t['tool_id']}\nName: {t['tool_name']}\nDescription: {t['tool_description']}\nGit Link: {t['tool_git_link']}"
        chunks.append(chunk)
    return chunks

def embed_chunks(chunks, openai_api_key):
    """Embed each chunk using LangChain + OpenAI."""
    embeddings = OpenAIEmbeddings(openai_api_key=openai_api_key)
    # Each chunk is embedded separately
    vectors = embeddings.embed_documents(chunks)
    # vectors is a list of embedding vectors, one per chunk
    return vectors

def main():
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        openai_api_key = input("Enter your OpenAI API key: ").strip()

    tools = parse_toon_table(TOON_FILE)
    chunks = chunk_tools(tools)
    print(f"Found {len(chunks)} entries. Embedding...")

    vectors = embed_chunks(chunks, openai_api_key)
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        print(f"Entry {i+1}:")
        print(chunk)
        print(f"Embedding: {vec[:8]}... (truncated)\n")  # Show just start

    # You may want to save vectors to a file, index, or database

if __name__ == "__main__":
    main()
