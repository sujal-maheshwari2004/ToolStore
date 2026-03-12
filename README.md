# ToolStorePy

**ToolStorePy** is an automatic **MCP (Model Context Protocol) server builder**.

Describe the tools you need in plain English. ToolStorePy finds the best matching implementations from a curated vector index, clones the repositories, audits them for security issues, and generates a single runnable MCP server — all in one command.

---

## ✨ What It Does

Given a `queries.json` file describing the tools you need:

```json
[
  { "tool_description": "evaluate a mathematical arithmetic expression securely" },
  { "tool_description": "convert between different units of measurement" },
  { "tool_description": "calculate cryptographic hash of a file" }
]
```

ToolStorePy will:

1. Resolve and download a vector index of curated tool repositories
2. Run semantic search + cross-encoder reranking to find the best tool per query
3. Clone matched repositories (served from a local bare-repo cache for speed)
4. Run a static AST security scan on every cloned repo
5. Show you a full security report and let you approve or skip flagged repos
6. Scan for `.env.example` files and merge them into a single `workspace/.env.example`
7. Validate your existing `workspace/.env` against required secrets if one exists
8. Parse `@tool`-decorated functions via AST and synthesise a unified MCP server
9. Print run commands and ask whether to launch the server immediately

**Output:**

```
toolstorepy_workspace/
├── mcp_unified_server.py   # your ready-to-run MCP server
├── security_report.txt     # full pre-build security scan report
├── .env.example            # merged secrets template (if any repos need secrets)
└── .venv/                  # isolated Python environment with MCP installed
```

---

## 📦 Installation

### Requirements

- Python ≥ 3.12
- Git installed and on `PATH`
- Internet access (for index download + repo cloning)

### Install

```bash
pip install .
```

Or in editable mode:

```bash
pip install -e .
```

---

## 🚀 Usage

### Basic Command

```bash
toolstorepy build \
  --queries queries.json \
  --index-url https://your-index-url.zip
```

Or using a built-in named index:

```bash
toolstorepy build \
  --queries queries.json \
  --index core-tools
```

---

## ⚙️ CLI Reference

### `build`

```bash
toolstorepy build --queries <path> [--index <name> | --index-url <url>] [options]
```

| Flag | Description |
|---|---|
| `--queries` | Path to `queries.json` (required) |
| `--index` | Name of a built-in tool index |
| `--index-url` | Direct URL to a downloadable index archive (.zip or .tar.gz) |
| `--workspace` | Workspace directory (default: `toolstorepy_workspace`) |
| `--install-requirements` | Install `requirements.txt` from each cloned repo into the workspace venv |
| `--force-refresh` | Re-download the index archive even if cached |
| `--verbose` | Enable verbose logging |

### `cache`

```bash
toolstorepy cache populate --queries <path> [--force]
toolstorepy cache list
toolstorepy cache clear
```

| Subcommand | Description |
|---|---|
| `populate` | Pre-cache repos from a `queries.json` without building |
| `list` | List all locally cached repositories |
| `clear` | Delete all cached repositories |

---

## 🔐 Security Scanning

Before building, ToolStorePy runs a static AST scan on every cloned repository and produces a report covering:

| Severity | What is checked |
|---|---|
| 🔴 HIGH | Shell/subprocess execution, `eval`/`exec`, outbound network requests, unsafe deserialisation (`pickle`, `yaml.load`) |
| 🟡 MEDIUM | File I/O, environment variable access, reflection (`getattr`, `setattr`, `globals`), insecure XML parsers |
| 🟢 LOW | Direct crypto primitives, deprecated modules, potential secret logging |

The full report is printed to the terminal and saved to `workspace/security_report.txt`.

For any repo with **HIGH** findings, you are asked individually whether to include it in the build or skip it. Skipped repos are excluded from the generated server and noted in a comment block at the top of `mcp_unified_server.py`.

---

## 🔑 Secret Management

If any cloned repo contains a `.env.example` file, ToolStorePy will:

- Merge all `.env.example` files into a single `workspace/.env.example`, grouped by repo with attribution comments
- Prompt you interactively to resolve any key conflicts (same key defined in multiple repos)
- Check your existing `workspace/.env` against the merged template and warn about any missing or empty keys
- List all required environment variables in both the terminal output and as a comment block at the top of `mcp_unified_server.py`

---

## 🏗️ How It Works

```
queries.json
      │
      ▼
Resolve & download vector index
      │
      ▼
Semantic search  (sentence-transformers)
      +
Cross-encoder reranking
      │
      ▼
Clone repositories  (bare-repo cache)
      │
      ▼
Static AST security scan  ──► security_report.txt
      │
      ▼  (user approves / skips flagged repos)
      │
      ▼
Merge .env.example files  ──► workspace/.env.example
      │
      ▼
Parse @tool functions via AST
      │
      ▼
Generate mcp_unified_server.py
      │
      ▼
Prompt: run now or run manually?
```

### Models

| Role | Default model |
|---|---|
| Embedding | `all-MiniLM-L6-v2` |
| Reranking | `cross-encoder/ms-marco-MiniLM-L-6-v2` |

Both can be overridden when instantiating `ToolStorePy` directly.

---

## ⚡ Repo Cache

Repositories are cloned once as bare repos into `~/.repo_cache` and reused across all future builds. This makes repeated builds near-instant.

```bash
# Pre-populate cache before a build
toolstorepy cache populate --queries queries.json

# See what's cached
toolstorepy cache list

# Wipe cache
toolstorepy cache clear
```

---

## 🧪 Evaluation Suite

ToolStorePy includes two evaluation scripts in `testing/`:

### `eval_RAG_Rerank.py`

Benchmarks retrieval + reranking accuracy across five query perturbation variants:

| Variant | What it does |
|---|---|
| `original` | Unmodified queries |
| `remove_token` | One random token removed per query |
| `add_token` | One random filler word inserted |
| `add_char` | One random character inserted into a token |
| `synonym` | A noun replaced with a synonym |

Produces 6 CSV reports + a summary including per-variant accuracy, robustness deltas, rerank score distributions, and flip analysis.

### `eval_build.py`

Stress-tests the full build pipeline in parallel across many tool subsets. Measures:

- Build success rate
- AST validity of generated servers
- Tool count per build
- Build timing (avg / median / min / max)

All broken down by subset size.

---

## 📁 Project Structure

```
toolstorepy/
├── cli.py                  # CLI entrypoint
├── orchestrator.py         # Main pipeline controller
├── config.py               # External library noise suppression
├── index/
│   ├── registry.py         # Built-in index name → URL resolution
│   └── downloader.py       # Index archive download + extraction
├── search/
│   ├── semantic.py         # Embedding + ChromaDB retrieval
│   └── rerank.py           # Cross-encoder reranking
├── loader/
│   ├── repo.py             # Repository cloning
│   └── cache.py            # Bare-repo cache management
├── builder/
│   ├── parser.py           # AST-based tool extraction
│   └── mcp_builder.py      # MCP server synthesis
├── utils/
│   ├── security_scanner.py # Static AST security analysis
│   └── env_merger.py       # .env.example merging + validation
└── testing/
    ├── eval_RAG_Rerank.py  # Retrieval + reranking evaluation
    └── eval_build.py       # Build pipeline evaluation
```

---

## 🧩 Extending ToolStorePy

| What you want to change | Where to look |
|---|---|
| Add a new built-in index | `index/registry.py` → `BUILTIN_INDEXES` |
| Change embedding or reranking model | `orchestrator.py` constructor |
| Add new security scan rules | `utils/security_scanner.py` → `IMPORT_RULES` / `CALL_RULES` |
| Change MCP server output format | `builder/mcp_builder.py` → `HEADER` / `FOOTER` / `_write_output` |
| Change tool decorator detection | `builder/parser.py` → `_is_tool_function` |

---

## 📚 Dependencies

| Package | Purpose |
|---|---|
| `chromadb` | Vector store for tool index |
| `sentence-transformers` | Embedding + cross-encoder reranking |
| `requests` | Index archive download |
| `mcp[cli]` | MCP server runtime (installed into workspace venv) |
| `pyyaml` | (upcoming) `toolstore.yaml` manifest parsing |

---

## 🗺️ Roadmap

- [ ] `toolstore.yaml` manifest support for multi-file tool repositories
- [ ] Public tool submission portal with LLM-based security auditing
- [ ] Versioned index publication with incremental ChromaDB updates
- [ ] `--dry-run` flag to preview tool selection without cloning or building
- [ ] Build manifest saved per run (which queries matched which repos, timestamps)
- [ ] `async def` tool function support in the parser
- [ ] Hardcoded secret detection in the security scanner

---

## 📜 License

MIT — Copyright (c) 2025 Sujal Maheshwari. See [LICENSE](LICENSE) for full terms.

---

## 🤝 Contributing

Contributions are welcome.

- Open issues for bugs or feature suggestions
- Submit pull requests
- Follow the existing module structure when adding new capabilities
