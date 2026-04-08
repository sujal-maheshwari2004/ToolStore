# ToolStorePy

**ToolStorePy** is an automatic **MCP (Model Context Protocol) server builder**.

Describe the tools you need in plain English. ToolStorePy finds the best matching implementations from a curated vector index, clones repositories, audits them for security issues, and generates a runnable MCP server — in one command.

---

# 📚 Documentation

Official documentation:

https://tool-store-py-docs.vercel.app/

Full architecture details, examples, and advanced usage are available there.

---

# 📦 Install from PyPI

```bash
pip install toolstorepy
```

PyPI project page:

https://pypi.org/project/toolstorepy/

---

# ✨ What It Does

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
2. Perform semantic retrieval + reranking
3. Clone matching repositories using a bare-repo cache
4. Run static AST security scans
5. Merge `.env.example` files if present
6. Validate required secrets
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

# 🚀 Quick Start

Example:

```bash
toolstorepy build \
  --queries queries.json \
  --index core-tools
```

Or with remote index:

```bash
toolstorepy build \
  --queries queries.json \
  --index-url https://your-index-url.zip
```

---

# ⚙️ CLI Reference

## build

```
toolstorepy build --queries <path> [options]
```

| Flag                   | Description               |
| ---------------------- | ------------------------- |
| --queries              | Path to queries.json      |
| --index                | Built-in index name       |
| --index-url            | Remote index archive      |
| --workspace            | Workspace directory       |
| --install-requirements | Install repo requirements |
| --force-refresh        | Re-download cached index  |
| --verbose              | Enable verbose logging    |

---

## cache

Populate repo cache:

```
toolstorepy cache populate --queries queries.json
```

List cached repositories:

```
toolstorepy cache list
```

Clear cache:

```
toolstorepy cache clear
```

---

# 🔐 Security Scanning

Each repository is scanned before inclusion using static AST analysis.

| Severity | Checks                                                                 |
| -------- | ---------------------------------------------------------------------- |
| HIGH     | subprocess execution, exec/eval, unsafe deserialization, network calls |
| MEDIUM   | filesystem access, environment variables, reflection                   |
| LOW      | deprecated modules, crypto primitives                                  |

Security report:

```
workspace/security_report.txt
```

Repositories flagged HIGH require manual approval before inclusion.

---

# 🔑 Secret Management

If tools include `.env.example`:

ToolStorePy automatically:

• merges templates
• resolves conflicts
• validates `.env` completeness
• documents required variables inside generated server

Output:

```
workspace/.env.example
```

---

# 🏗️ Pipeline Overview

```
queries.json
      │
      ▼
vector index retrieval
      │
      ▼
semantic search
      │
      ▼
cross-encoder reranking
      │
      ▼
repository cloning
      │
      ▼
AST security scanning
      │
      ▼
.env merge + validation
      │
      ▼
tool extraction
      │
      ▼
MCP server synthesis
```

---

# ⚡ Repository Cache

Repositories are cached locally:

```
~/.repo_cache
```

Reuse across builds significantly reduces runtime.

---

# 🧪 Evaluation Suite

Located in:

```
testing/
```

Includes:

### eval_RAG_Rerank.py

Benchmarks retrieval robustness across perturbations.

Outputs:

• CSV metrics
• accuracy deltas
• reranking score distributions

---

### eval_build.py

Stress-tests pipeline performance across subsets:

Measures:

• build success rate
• AST validity
• tool counts
• build timing statistics

---

# 📁 Project Structure

```
toolstorepy/
├── cli.py
├── orchestrator.py
├── config.py
├── index/
├── search/
├── loader/
├── builder/
├── utils/
└── testing/
```

---

# 🧩 Extending ToolStorePy

| Change                     | Location                  |
| -------------------------- | ------------------------- |
| Add built-in index         | index/registry.py         |
| Change embedding model     | orchestrator.py           |
| Add security rules         | utils/security_scanner.py |
| Modify MCP output          | builder/mcp_builder.py    |
| Adjust decorator detection | builder/parser.py         |

---

# 🗺️ Roadmap

Planned:

* toolstore.yaml manifest support
* public tool submission portal
* versioned index publication
* dry-run preview mode
* build manifest export
* async tool support
* hardcoded secret detection

---

# 📜 License

MIT License
Copyright (c) 2025 Sujal Maheshwari

See:

```
LICENSE
```

---

# 🤝 Contributing

Contributions welcome.

Open issues or submit pull requests following the existing module structure.
