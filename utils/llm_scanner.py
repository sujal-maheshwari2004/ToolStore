"""
utils/llm_scanner.py

Model-agnostic LLM security scanner for cloned tool repositories.

Uses LangChain's init_chat_model so any supported provider works
(Anthropic, OpenAI, Google, Mistral, Cohere, etc.).  The caller passes
a model string; LangChain infers the provider automatically from
well-known prefixes (e.g. "claude-sonnet-4-6", "gpt-4o",
"gemini-2.0-flash") or the user can be explicit with
"provider:model-name" syntax.

Structured output is obtained via .with_structured_output(Pydantic model)
so the verdict schema is enforced at the LangChain layer -- no manual JSON
parsing.

Required env vars depend on the chosen model:
    Anthropic   -> ANTHROPIC_API_KEY
    OpenAI      -> OPENAI_API_KEY
    Google      -> GOOGLE_API_KEY
    Mistral     -> MISTRAL_API_KEY
    ...etc.

LangChain will raise a clear error if the relevant key is missing or the
integration package is not installed.

Token budget
------------
Files are concatenated up to MAX_REPO_CHARS (~80k chars / ~20k tokens)
with a per-file cap of MAX_FILE_CHARS (12k chars).  Truncation is noted
in the prompt so the model can factor uncertainty into confidence.
"""

import os
import logging
from pathlib import Path
from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from .security_scanner import Finding, RepoReport

logger = logging.getLogger("ToolStorePy")

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

DEFAULT_MODEL   = "claude-sonnet-4-6"
MAX_FILE_CHARS  = 12_000
MAX_REPO_CHARS  = 80_000

_SKIP_DIRS = {
    "test", "tests", "testing",
    "examples", "example",
    "docs", "doc",
    ".git", "__pycache__",
    ".venv", "venv", "env",
    "build", "dist", ".eggs",
}
_SKIP_FILES = {"setup.py", "conftest.py"}

# ---------------------------------------------------------------------------
# Structured output schema (Pydantic)
# ---------------------------------------------------------------------------

class LLMFinding(BaseModel):
    """A single security finding identified by the LLM reviewer."""
    severity: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="Risk level: HIGH = code execution / exfiltration risk; "
                    "MEDIUM = suspicious capability; LOW = informational."
    )
    file: str = Field(
        description="Relative file path, or 'repo-level' if not file-specific."
    )
    line: int = Field(
        default=0,
        description="Approximate line number, 0 if unknown."
    )
    category: str = Field(
        description="Short label, e.g. 'Shell injection', 'Credential logging'."
    )
    detail: str = Field(
        description="What the risk is, why it matters, and where in the code."
    )


class LLMVerdict(BaseModel):
    """Security verdict for a tool repository."""
    verdict: Literal["INCLUDE", "SKIP"] = Field(
        description=(
            "INCLUDE: safe to bake into the MCP server. "
            "SKIP: exclude due to security risk."
        )
    )
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description=(
            "Confidence in the verdict. LOW if significant code was truncated "
            "or the repo is highly complex."
        )
    )
    summary: str = Field(
        description="One sentence explaining the decision."
    )
    findings: List[LLMFinding] = Field(
        default_factory=list,
        description=(
            "Security findings. May be empty for clean INCLUDE verdicts. "
            "Do NOT flag normal patterns like requests.get, open(), logging, "
            "subprocess.run without shell=True, or argparse as HIGH."
        )
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a security reviewer for ToolStorePy, an automated MCP "
    "(Model Context Protocol) server builder. Your task is to review "
    "Python source code from a third-party tool repository and decide "
    "whether it is safe to include in a generated server.\n\n"
    "Verdict rules:\n"
    "  SKIP  — if you find: arbitrary code execution (eval/exec/compile), "
    "shell injection, unsafe deserialization of untrusted input, credential "
    "exfiltration, backdoors, obfuscated or encoded payloads, or clear "
    "malicious intent.\n"
    "  INCLUDE — if the code is a reasonable tool implementation using "
    "expected patterns (HTTP requests, file I/O, standard library, CLI "
    "argument parsing, logging).\n\n"
    "Do NOT flag as HIGH: requests.get, open(), print(), logging calls, "
    "subprocess.run/Popen without shell=True, hashlib, argparse, or any "
    "other routine Python pattern.\n\n"
    "Be decisive. If significant code was truncated, lower your confidence "
    "to MEDIUM or LOW but still give a verdict."
)

# ---------------------------------------------------------------------------
# Source collection
# ---------------------------------------------------------------------------

def _collect_repo_source(repo_dir: Path) -> Tuple[str, int, int]:
    """
    Concatenate Python source files up to MAX_REPO_CHARS.
    Returns (source, files_included, files_truncated).
    """
    parts: List[str] = []
    total = 0
    included = 0
    truncated = 0

    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
        root_path = Path(root)

        for fname in sorted(files):
            if not fname.endswith(".py") or fname in _SKIP_FILES:
                continue
            if total >= MAX_REPO_CHARS:
                truncated += 1
                continue

            fpath = root_path / fname
            rel   = str(fpath.relative_to(repo_dir))
            try:
                raw = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            if len(raw) > MAX_FILE_CHARS:
                raw = raw[:MAX_FILE_CHARS] + f"\n... [file truncated at {MAX_FILE_CHARS} chars]"

            chunk     = f"### {rel}\n{raw}\n"
            remaining = MAX_REPO_CHARS - total

            if len(chunk) > remaining:
                chunk = chunk[:remaining] + "\n... [repo budget exhausted]"
                parts.append(chunk)
                total += len(chunk)
                truncated += 1
                break

            parts.append(chunk)
            total += len(chunk)
            included += 1

    return "\n".join(parts), included, truncated


# ---------------------------------------------------------------------------
# LangChain call
# ---------------------------------------------------------------------------

def _call_llm(
    repo_name: str,
    source: str,
    files_included: int,
    files_truncated: int,
    model_name: str,
) -> LLMVerdict:
    """
    Send the repo source to the configured LLM and return a validated
    LLMVerdict.  Raises RuntimeError on import / API failure.
    """
    try:
        from langchain.chat_models import init_chat_model
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError as exc:
        raise RuntimeError(
            "LangChain is required for LLM scanning. "
            "Install it with: pip install langchain langchain-core\n"
            "Then install the integration package for your model provider, e.g.:\n"
            "  pip install langchain-anthropic   # for Claude\n"
            "  pip install langchain-openai      # for GPT\n"
            "  pip install langchain-google-genai # for Gemini"
        ) from exc

    try:
        llm = init_chat_model(model_name, temperature=0)
        structured_llm = llm.with_structured_output(LLMVerdict)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to initialise model '{model_name}': {exc}\n"
            "Check that the provider integration package is installed and "
            "the relevant API key environment variable is set."
        ) from exc

    truncation_note = (
        f"\nNote: {files_truncated} file(s) were omitted or truncated "
        f"due to the token budget. Reflect this in your confidence level.\n"
        if files_truncated else ""
    )

    user_content = (
        f"Repository: {repo_name}\n"
        f"Files reviewed: {files_included}"
        f"{truncation_note}\n\n"
        f"{source}"
    )

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]

    try:
        verdict: LLMVerdict = structured_llm.invoke(messages)
    except Exception as exc:
        raise RuntimeError(
            f"LLM invocation failed for repo '{repo_name}': {exc}"
        ) from exc

    return verdict


# ---------------------------------------------------------------------------
# Per-repo scan
# ---------------------------------------------------------------------------

def llm_scan_repo(
    repo_dir: Path,
    model_name: str = DEFAULT_MODEL,
) -> Tuple[RepoReport, str]:
    """
    Run LLM security scan on a single repo.

    Returns:
        (RepoReport, verdict_string)
        verdict_string is "INCLUDE" or "SKIP".

    On API/import failure the report contains a MEDIUM finding describing
    the error and verdict defaults to "INCLUDE" (fail-open so a missing
    package doesn't silently kill all repos).
    """
    report  = RepoReport(repo_name=repo_dir.name)
    verdict = "INCLUDE"

    source, files_included, files_truncated = _collect_repo_source(repo_dir)

    if not source.strip():
        logger.warning(
            f"[LLM-SCAN] No Python source found in {repo_dir.name} -- skipping."
        )
        return report, verdict

    logger.info(
        f"[LLM-SCAN] Reviewing {repo_dir.name} "
        f"({files_included} files, model={model_name})..."
    )

    try:
        result = _call_llm(
            repo_name=repo_dir.name,
            source=source,
            files_included=files_included,
            files_truncated=files_truncated,
            model_name=model_name,
        )
    except RuntimeError as exc:
        logger.error(f"[LLM-SCAN] {repo_dir.name}: {exc}")
        report.findings.append(Finding(
            severity="MEDIUM",
            category="[LLM] Scan error",
            detail=str(exc),
            file="(llm-scanner)",
            line=0,
        ))
        return report, "INCLUDE"   # fail-open

    verdict = result.verdict

    logger.info(
        f"[LLM-SCAN] {repo_dir.name}: {verdict} "
        f"(confidence={result.confidence}) -- {result.summary}"
    )

    # Convert LLMFinding objects into Finding dataclass instances.
    # Category is prefixed with [LLM] so they're distinct from AST findings
    # in the security report.
    for f in result.findings:
        report.findings.append(Finding(
            severity=f.severity,
            category=f"[LLM] {f.category}",
            detail=f.detail,
            file=f.file,
            line=f.line,
        ))

    # Always append a summary finding so the report is self-explanatory.
    report.findings.append(Finding(
        severity="LOW",
        category="[LLM] Verdict",
        detail=f"verdict={verdict}, confidence={result.confidence}. {result.summary}",
        file="(llm-scanner)",
        line=0,
    ))

    return report, verdict


# ---------------------------------------------------------------------------
# Batch scan
# ---------------------------------------------------------------------------

def llm_scan_all_repos(
    tools_dir: Path,
    model_name: str = DEFAULT_MODEL,
) -> Tuple[List[RepoReport], List[str], List[str]]:
    """
    Run LLM scan on every repo under tools_dir.

    Returns:
        (reports, allowed_repos, skipped_repos)
    """
    reports:  List[RepoReport] = []
    allowed:  List[str]        = []
    skipped:  List[str]        = []

    for repo_dir in sorted(tools_dir.iterdir()):
        if not repo_dir.is_dir() or repo_dir.name.startswith("."):
            continue
        report, verdict = llm_scan_repo(repo_dir, model_name=model_name)
        reports.append(report)
        if verdict == "SKIP":
            skipped.append(repo_dir.name)
        else:
            allowed.append(repo_dir.name)

    return reports, allowed, skipped