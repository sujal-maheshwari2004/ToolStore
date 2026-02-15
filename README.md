# ToolStorePy

**ToolStorePy** is an automatic **MCP (Model Context Protocol) tool builder**.

It allows you to:

* 🔎 Search a semantic tool index using natural language queries
* 📦 Automatically clone the best-matching repositories
* 🧠 Extract `@tool`-decorated functions
* 🏗 Generate a unified MCP server from selected tools

All in a single command.

---

## ✨ What It Does

Given a `queries.json` file describing the tools you need:

```json
[
  {
    "tool_description": "Tool for converting PDF to text"
  },
  {
    "tool_description": "Tool for extracting YouTube transcripts"
  }
]
```

ToolStorePy will:

1. Resolve a vector index (built-in or custom URL)
2. Download and extract the index
3. Run semantic search + reranking
4. Select the best tool per query
5. Clone matched repositories
6. Parse `@tool` functions
7. Generate a unified MCP server

Output:

```
toolstorepy_workspace/mcp_unified_server.py
```

---

# 📦 Installation

## Requirements

* Python ≥ 3.12
* Git installed
* Internet access (for index + repo cloning)

## Install

```bash
pip install .
```

Or install in editable mode:

```bash
pip install -e .
```

---

# 🚀 Usage

## Basic Command

```bash
toolstorepy build \
  --queries queries.json \
  --index-url https://your-index-url.zip
```

Or use a built-in index:

```bash
toolstorepy build \
  --queries queries.json \
  --index core-tools
```

---

## CLI Options

| Flag                     | Description                                            |
| ------------------------ | ------------------------------------------------------ |
| `--queries`              | Path to `queries.json` (required)                      |
| `--index`                | Name of built-in index                                 |
| `--index-url`            | Direct URL to downloadable index archive               |
| `--workspace`            | Workspace directory (default: `toolstorepy_workspace`) |
| `--install-requirements` | Install `requirements.txt` from cloned repos           |
| `--force-refresh`        | Re-download index archive                              |
| `--verbose`              | Enable verbose logging                                 |

---

# 🏗 How It Works

## 1️⃣ Index Resolution

Uses:

* Built-in registry (`index/registry.py`)
* Or direct `--index-url`

## 2️⃣ Semantic Search

Powered by:

* `sentence-transformers`
* `chromadb`
* Cross-encoder reranking

Default models:

* Encoder: `all-MiniLM-L6-v2`
* Cross-encoder: `cross-encoder/ms-marco-MiniLM-L-6-v2`

## 3️⃣ Repository Processing

Each selected repository is:

* Cloned into workspace
* Optionally installs `requirements.txt`

## 4️⃣ AST Parsing

ToolStorePy statically parses Python files to extract:

* Structured imports
* Utility functions
* Classes
* Global assignments
* `@tool`-decorated functions

Duplicate tool names are resolved automatically.

## 5️⃣ MCP Server Generation

Produces:

```python
mcp_unified_server.py
```

With:

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("UtilityTools")
```

All tools are registered and runnable via:

```bash
python mcp_unified_server.py
```

---

# 📁 Project Structure

```
toolstorepy/
├── cli.py              # CLI entrypoint
├── orchestrator.py     # Main pipeline controller
├── index/              # Index resolution + downloading
├── search/             # Semantic search + reranking
├── loader/             # Repo cloning
├── builder/            # AST parsing + MCP synthesis
└── utils/
```

---

# 🧠 Architecture Overview

```
queries.json
      ↓
Resolve index
      ↓
Download vector DB
      ↓
Semantic search
      ↓
Clone repositories
      ↓
Parse tools via AST
      ↓
Generate unified MCP server
```

---

# ⚠️ Notes

* One best tool is selected per query.
* Only `@tool`-decorated functions are exposed.
* Relative imports inside repositories are ignored.
* Duplicate tool names are deduplicated.

---

# 🛠 Example Workflow

```bash
# 1️⃣ Create queries.json
echo '[{"tool_description": "convert pdf to text"}]' > queries.json

# 2️⃣ Build MCP server
toolstorepy build \
  --queries queries.json \
  --index-url https://example.com/index.zip

# 3️⃣ Run MCP server
python toolstorepy_workspace/mcp_unified_server.py
```

---

# 📚 Dependencies

Core dependencies:

* `chromadb`
* `sentence-transformers`
* `requests`

See `requirements.txt` for full list.

---

# 🧩 Extending ToolStorePy

You can:

* Add new built-in indexes in `index/registry.py`
* Change embedding models in `orchestrator.py`
* Modify parsing logic in `builder/parser.py`
* Customize MCP output in `builder/mcp_builder.py`

---

# 📜 License

Add your preferred license here.

---

# 🤝 Contributing

Contributions are welcome.

* Open issues for bugs or improvements
* Submit pull requests
* Suggest architecture enhancements
