# 🔧 ToolShop — Automatic MCP Tool Builder (PoC)

ToolShop is a **proof-of-concept pipeline** that automatically builds a unified MCP (Model Context Protocol) tool server starting from **plain-text tool descriptions**.

You provide:
- natural-language queries (e.g., *"a calculator that can do basic math"*),
- a vector database of existing tools,
- and a set of GitHub repositories containing actual tool implementations.

ToolShop automatically:
1. **Matches** each query to the most relevant tool using semantic search (SentenceTransformers + ChromaDB)
2. **Clones** the corresponding GitHub repositories
3. **Extracts** `@tool`-decorated Python functions from the repos
4. **Generates** a single unified MCP server: `mcp_unified_server.py`
5. **Runs** a LangGraph + Ollama REACT agent that can use all discovered tools via MCP

This project demonstrates what an **automatic tool-orchestration system** could look like.

> ⚠️ **This is a proof of concept.**  
> It is intentionally simple, may break on complex repositories, and is not meant for production.

---

## 🧠 High-Level Architecture

```
queries.json  →  semantic search  →  tool_matches.json
                     │
                     ▼
                clone repos
                     │
                     ▼
     scan .py files for @tool functions
                     │
                     ▼
         build unified mcp_unified_server.py
                     │
                     ▼
       LangGraph + Ollama Agent runs with tools
```

---

## 🚀 Features (PoC Scope)

### ✅ Semantic Search Over Tools
- Embeds tool metadata from a `.toon` file  
- Stores everything in a local ChromaDB  
- Uses SentenceTransformers + CrossEncoder reranking  

### ✅ Automatic Repo Cloning
- Given a GitHub link → clones into `/tools`  
- Installs repo requirements if available  

### ✅ MCP Tool Extraction
- Parses Python source code using `ast`  
- Collects safe imports, utility functions, and MCP-decorated tools  
- Combines all into **one unified MCP server file**  

### ✅ LangGraph REACT Agent
- Uses `MultiServerMCPClient`
- Automatically calls tools using MCP
- Supports iterative tool usage until completion

---

## 📂 Project Structure

```
.
├── main.py                           # LangGraph + Ollama agent
├── queries.json                      # Natural-language tool queries
├── query_matches.json                # Semantic search results
│
├── mcp_unified_server.py             # Auto-generated MCP server
│
├── tools/                            # Auto-cloned repos live here
│
├── main_pipeline/
│   ├── tool_store.py                 # Full pipeline driver
│   ├── mcp_builder.py                # Builds unified MCP server
│   ├── tool_loader.py                # Git clone + install
│   ├── sementic_search.py            # Query → repo matching
│
├── vector_db_creation/
│   ├── embed_toon.py                 # Build ChromaDB
│   ├── tools.toon                    # Tool metadata table
│
├── requirements.txt
└── pyproject.toml
```

---

## ⚙️ Quickstart

### 1. Create environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Build vector database

```bash
cd vector_db_creation
python embed_toon.py
cd ..
```

### 3. Run the whole tool pipeline

```bash
python main_pipeline/tool_store.py
```

This produces:

```
query_matches.json
tools/ (cloned repos)
mcp_unified_server.py
```

### 4. Run LangGraph agent with all tools enabled

```bash
python main.py
```

Example:

```
>>> [TOOL CALL] Agent is using tool 'calculator' with args {'expression': '2+2'}
AI: 4
```

---

## 🧪 Example Queries

```
calculate 2 + 2
convert 10 meters to feet
show me CPU usage
generate a random token
summarize this text...
```

---

## 📜 Notes & Limitations

- This is a **prototype**, not production ready.  
- Repos must contain MCP-style decorated functions.  
- Import conflicts, missing requirements, and name collisions are not fully resolved.  
- Certain tools (Weather, Currency) may require external API keys.

---

## 💡 Why This Exists

This project explores the idea of:

> **“Automatic tool discovery and toolchain assembly, powered by semantic search + agentic reasoning.”**

Instead of hand-wiring tools, the agent learns what tools exist based purely on:
- embeddings  
- metadata  
- GitHub source code  

It’s a step toward **self-upgrading AI systems**.

---

## 🤝 Contributing

PRs, suggestions, improvements — all welcome.  
This PoC can evolve into:

- a full MCP package manager  
- a tool orchestrator  
- a dynamic tool synthesizer  
- or a plug-and-play agent runtime  

---

## 📛 License

MIT License.
