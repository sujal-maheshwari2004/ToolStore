import os
import json

from sementic_search import read_queries, run_query_batch
from tool_loader import process_tools
from mcp_builder import build_unified_server


CHROMA_DIR = "../toon_chroma_db"
MODEL_NAME = "all-MiniLM-L6-v2"
CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
MATCHES_FILE = "../query_matches.json"


def tool_store(query_json_path: str):
    """
    Full pipeline:
    1. Semantic search → query_matches.json
    2. Clone repos from query_matches.json
    3. Build unified MCP server file
    """

    if not os.path.exists(query_json_path):
        raise FileNotFoundError(f"Query file not found: {query_json_path}")

    print("\n=== STEP 1: Running Semantic Search ===")

    queries = read_queries(query_json_path)
    results = run_query_batch(
        queries=queries,
        persist_dir=CHROMA_DIR,
        encoder_model=MODEL_NAME,
        cross_encoder_model=CROSS_ENCODER_MODEL,
        top_k=10
    )

    with open(MATCHES_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("Semantic search complete → query_matches.json created.")

    print("\n=== STEP 2: Cloning Tool Repos ===")
    process_tools(MATCHES_FILE)

    print("\n=== STEP 3: Building Unified MCP Server ===")
    final_file = build_unified_server()

    print(f"\n🎉 Pipeline complete! Unified MCP server: {final_file}")


if __name__ == "__main__":
    tool_store("../queries.json")
