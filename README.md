# ToolStorePy

**ToolStorePy** is an automatic **MCP (Model Context Protocol) server builder**.

Describe the tools you need in plain English. ToolStorePy finds the best matching implementations from a curated vector index, clones the repositories, audits them for security, and generates a ready-to-run MCP server — in one command.

---

## 📚 Documentation

Full architecture details, examples, and advanced usage:

https://tool-store-py-docs.vercel.app/

---

## 📦 Install

```bash
pip install toolstorepy
```

PyPI: https://pypi.org/project/toolstorepy/

---

## ✨ What It Does

Given a `queries.json` file:

```json
[
  { "tool_description": "evaluate a mathematical arithmetic expression securely" },
  { "tool_description": "convert between different units of measurement" },
  { "tool_description": "calculate cryptographic hash of a file" }
]
```

Run:

```bash
toolstorepy build --queries queries.json --index core-tools
```

ToolStorePy will:

1. Download a vector index of curated tool repositories
2. Run semantic retrieval + cross-encoder reranking
3. Clone matching repositories via a local bare-repo cache
4. Run a static AST security scan on every repo
5. Optionally run an LLM-based security review (autonomous, no human prompt)
6. Merge `.env.example` files and validate required secrets
7. Extract `@tool` functions via AST
8. Generate a unified MCP server
9. Optionally launch the server immediately

Output:

```
toolstorepy_workspace/
├── mcp_unified_server.py
├── security_report.txt
├── .env.example
└── .venv/
```

---

## 🚀 Quick Start

```bash
# Using the built-in index
toolstorepy build \
  --queries queries.json \
  --index core-tools

# Using a remote index URL
toolstorepy build \
  --queries queries.json \
  --index-url https://your-index-url.zip

# Custom port
toolstorepy build \
  --queries queries.json \
  --index core-tools \
  --port 9090

# LLM-based security scan (autonomous, no human prompt)
toolstorepy build \
  --queries queries.json \
  --index core-tools \
  --llm-scan
```

---

## ⚙️ CLI Reference

### build

```
toolstorepy build --queries <path> [options]
```

| Flag | Default | Description |
|---|---|---|
| `--queries` | required | Path to queries.json |
| `--index` | — | Built-in index name |
| `--index-url` | — | Remote index archive URL |
| `--workspace` | `toolstorepy_workspace` | Workspace directory |
| `--install-requirements` | off | Install repo requirements.txt files |
| `--host` | `0.0.0.0` | Host the MCP server binds on |
| `--port` | `8000` | Port the MCP server listens on |
| `--llm-scan` | off | Enable LLM-based autonomous security review |
| `--llm-model` | `claude-sonnet-4-6` | Model for LLM scanning (any LangChain-supported string) |
| `--force-refresh` | off | Re-download cached index |
| `--verbose` | off | Verbose logging + full tracebacks |

### cache

```bash
# Pre-warm cache from a resolved queries file (items must have git_link)
toolstorepy cache populate --queries resolved.json

# Pre-warm cache from explicit URLs
toolstorepy cache populate --url https://github.com/org/repo1.git \
                           --url https://github.com/org/repo2.git

# List cached repos
toolstorepy cache list

# Clear cache
toolstorepy cache clear
```

---

## 🔐 Security Scanning

Every repository is scanned before inclusion. Two modes are available:

### AST scan (default)

Static analysis using Python's `ast` module. Fast and deterministic.

| Severity | What triggers it |
|---|---|
| HIGH | `eval`/`exec`, `os.system`, `subprocess` with `shell=True`, `pickle.loads`, `yaml.load` without `Loader=` |
| MEDIUM | Capability imports (`subprocess`, `pickle`, raw sockets, unsafe XML parsers), dynamic reflection |
| LOW | HTTP clients, crypto primitives, deprecated modules |

Repos with HIGH findings prompt you to include or skip before the build proceeds.

### LLM scan (`--llm-scan`)

An LLM reviews the full source of each repo and makes an autonomous `INCLUDE`/`SKIP` decision. The human prompt is bypassed entirely — the build proceeds automatically based on the LLM verdict.

Both scanners can be used together. When `--llm-scan` is active, AST findings are merged into the same security report and the LLM decision is final.

#### Model-agnostic via LangChain

The LLM scanner uses [LangChain's `init_chat_model`](https://python.langchain.com/docs/how_to/chat_models_universal_init/) so any supported provider works:

```bash
# Claude (default)
export ANTHROPIC_API_KEY=sk-...
toolstorepy build --queries q.json --index core-tools --llm-scan

# GPT-4o
export OPENAI_API_KEY=sk-...
toolstorepy build ... --llm-scan --llm-model gpt-4o

# Gemini
export GOOGLE_API_KEY=...
toolstorepy build ... --llm-scan --llm-model gemini-2.0-flash
```

Install the integration package for your chosen provider:

```bash
pip install langchain-anthropic    # Claude  → ANTHROPIC_API_KEY
pip install langchain-openai       # GPT     → OPENAI_API_KEY
pip install langchain-google-genai # Gemini  → GOOGLE_API_KEY
```

The security report (`workspace/security_report.txt`) is always written regardless of which scan mode is used. LLM findings are tagged `[LLM]` in the report so they're visually distinct from AST findings.

---

## 🔑 Secret Management

If any cloned tool includes a `.env.example`, ToolStorePy automatically:

- Merges all `.env.example` files across repos
- Resolves conflicts interactively
- Validates completeness against an existing `.env`
- Documents required keys at the top of the generated server file

Output: `workspace/.env.example`

Steps:

1. Copy `.env.example` → `.env` in your workspace
2. Fill in the required values
3. Re-run the server

---

## 🌐 Server Configuration

The generated server runs on `streamable-http` transport. Host and port are baked in at build time:

```bash
# Default: 0.0.0.0:8000
toolstorepy build --queries q.json --index core-tools

# Custom host/port
toolstorepy build --queries q.json --index core-tools --host 127.0.0.1 --port 9090
```

The generated `mcp_unified_server.py` contains:

```python
if __name__ == "__main__":
    mcp.run(transport='streamable-http', host='127.0.0.1', port=9090)
```

---

## 🏗️ Pipeline Overview

```
queries.json
      │
      ▼
vector index retrieval
      │
      ▼
semantic search + reranking
      │
      ▼
repository cloning (bare-repo cache)
      │
      ▼
AST security scan
      │
      ▼
LLM security scan (optional, --llm-scan)
      │
      ▼
.env merge + validation
      │
      ▼
@tool extraction via AST
      │
      ▼
MCP server synthesis
```

---

## ⚡ Repository Cache

Repositories are cached as bare git clones at:

```
~/.repo_cache/
```

Subsequent builds reuse the cache, skipping remote clones entirely. Pre-warm it manually with:

```bash
toolstorepy cache populate --url https://github.com/org/repo.git
```

---

## 📁 Project Structure

```
toolstorepy/
├── cli.py
├── orchestrator.py
├── config.py
├── builder/
│   ├── mcp_builder.py
│   └── parser.py
├── index/
│   ├── downloader.py
│   └── registry.py
├── loader/
│   ├── cache.py
│   └── repo.py
├── search/
│   ├── rerank.py
│   └── semantic.py
└── utils/
    ├── env_merger.py
    ├── llm_scanner.py
    └── security_scanner.py
```

---

## 🧩 Extending ToolStorePy

| Goal | Location |
|---|---|
| Add a built-in index | `index/registry.py` |
| Change embedding model | `orchestrator.py` |
| Add AST security rules | `utils/security_scanner.py` |
| Tune LLM scan prompt | `utils/llm_scanner.py` |
| Modify MCP server output | `builder/mcp_builder.py` |
| Adjust `@tool` detection | `builder/parser.py` |

---

## 🗺️ Roadmap

- `toolstore.yaml` manifest support
- Public tool submission portal
- Versioned index publication
- Dry-run preview mode
- Build manifest export (cross-build pollution fix)
- Async tool support
- Hardcoded secret detection in AST scanner
- Aliased-import tracking in security scanner

---

## 📜 License

MIT License — Copyright (c) 2025 Sujal Maheshwari

See `LICENSE` for full terms. Attribution required in public-facing derivative works.

---

## 🤝 Contributing

Contributions welcome. Open issues or submit pull requests following the existing module structure.

https://github.com/sujal-maheshwari2004/toolstorepy