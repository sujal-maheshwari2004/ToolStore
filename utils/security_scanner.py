"""
utils/security_scanner.py

Static AST-based security scanner for cloned tool repositories.

Rules are calibrated so that HIGH findings always indicate a concrete
code-execution or RCE risk -- not just "this module exists in your
imports." Common building blocks like `requests`, `hashlib`, or `print()`
are LOW or not flagged at all. The build prompt only fires on HIGH.

Severity tiers
--------------
HIGH    Actual dangerous calls: eval/exec/compile/__import__,
        os.system/os.popen/os.exec*/os.spawn*,
        subprocess.* with shell=True (plus subprocess.getoutput/
        getstatusoutput, which always shell out),
        pickle.loads/load, marshal.loads/load,
        yaml.load without Loader=.

MEDIUM  Capability imports (subprocess, pickle, raw network modules,
        unsafe XML parsers) and dynamic reflection (getattr/setattr/
        delattr/vars called with a non-literal name).

LOW     HTTP-client / crypto-primitive / deprecated-module imports.
        Informational only.

Known limitations
-----------------
- Aliased imports defeat name-based detection: `from yaml import load as
  L; L(...)` is not caught. Tracking import aliases is a larger change.
- Only top-level module names are inspected against the import rules; a
  star-imported symbol used directly (e.g. `from os import system; system(...)`)
  is flagged via the call rule, but `from os import system as do` is not.
"""

import ast
import os
import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ============================================================
# DATA STRUCTURES
# ============================================================

SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


@dataclass
class Finding:
    severity: str   # HIGH | MEDIUM | LOW
    category: str
    detail:   str
    file:     str
    line:     int


@dataclass
class RepoReport:
    repo_name: str
    findings:  List[Finding] = field(default_factory=list)

    @property
    def high_count(self)   -> int: return sum(1 for f in self.findings if f.severity == "HIGH")
    @property
    def medium_count(self) -> int: return sum(1 for f in self.findings if f.severity == "MEDIUM")
    @property
    def low_count(self)    -> int: return sum(1 for f in self.findings if f.severity == "LOW")
    @property
    def is_clean(self)     -> bool: return len(self.findings) == 0


# ============================================================
# RULE TABLES
# ============================================================

# (module_prefix, severity, category, detail_template)
# Matched as exact module name or as a sub-module
# (e.g. "urllib" matches "urllib", "urllib.request", etc.).
IMPORT_RULES: List[Tuple[str, str, str, str]] = [
    # MEDIUM -- capability surfaces worth audit
    ("subprocess",   "MEDIUM", "Shell capability",
        "Imports '{mod}' (shell/process execution capability)"),
    ("commands",     "MEDIUM", "Shell capability",
        "Imports '{mod}' (deprecated; shell execution capability)"),

    ("pickle",       "MEDIUM", "Deserialization capability",
        "Imports '{mod}' (can deserialize arbitrary objects)"),
    ("cPickle",      "MEDIUM", "Deserialization capability",
        "Imports '{mod}' (can deserialize arbitrary objects)"),
    ("marshal",      "MEDIUM", "Deserialization capability",
        "Imports '{mod}' (can deserialize arbitrary objects)"),
    ("shelve",       "MEDIUM", "Deserialization capability",
        "Imports '{mod}' (pickle-backed key-value store)"),

    ("socket",       "MEDIUM", "Raw network",
        "Imports '{mod}' (low-level network sockets)"),
    ("socketserver", "MEDIUM", "Raw network",
        "Imports '{mod}' (low-level network server)"),
    ("ftplib",       "MEDIUM", "Raw network",
        "Imports '{mod}' (FTP client)"),
    ("smtplib",      "MEDIUM", "Raw network",
        "Imports '{mod}' (SMTP client)"),
    ("telnetlib",    "MEDIUM", "Raw network",
        "Imports '{mod}' (Telnet client; deprecated)"),
    ("imaplib",      "MEDIUM", "Raw network",
        "Imports '{mod}' (IMAP client)"),
    ("poplib",       "MEDIUM", "Raw network",
        "Imports '{mod}' (POP3 client)"),
    ("nntplib",      "MEDIUM", "Raw network",
        "Imports '{mod}' (NNTP client; deprecated)"),

    ("xml.etree",    "MEDIUM", "XML parsing (potentially unsafe)",
        "Imports '{mod}' (may be vulnerable to XXE; consider defusedxml)"),
    ("xml.sax",      "MEDIUM", "XML parsing (potentially unsafe)",
        "Imports '{mod}' (may be vulnerable to XXE; consider defusedxml)"),
    ("xml.dom",      "MEDIUM", "XML parsing (potentially unsafe)",
        "Imports '{mod}' (may be vulnerable to XXE; consider defusedxml)"),
    ("lxml",         "MEDIUM", "XML parsing (potentially unsafe)",
        "Imports '{mod}' (use defusedxml.lxml or set resolve_entities=False)"),

    # LOW -- common and expected; informational only
    ("requests",     "LOW", "HTTP client",
        "Imports '{mod}' (outbound HTTP)"),
    ("urllib",       "LOW", "HTTP client",
        "Imports '{mod}' (outbound HTTP)"),
    ("urllib2",      "LOW", "HTTP client",
        "Imports '{mod}' (outbound HTTP; Py2-era)"),
    ("urllib3",      "LOW", "HTTP client",
        "Imports '{mod}' (outbound HTTP)"),
    ("http.client",  "LOW", "HTTP client",
        "Imports '{mod}' (outbound HTTP)"),
    ("httpx",        "LOW", "HTTP client",
        "Imports '{mod}' (outbound HTTP)"),
    ("aiohttp",      "LOW", "HTTP client",
        "Imports '{mod}' (outbound HTTP)"),

    ("hashlib",      "LOW", "Cryptographic primitives",
        "Imports '{mod}' (ensure correct algorithm and usage)"),
    ("hmac",         "LOW", "Cryptographic primitives",
        "Imports '{mod}' (ensure correct algorithm and usage)"),
    ("ssl",          "LOW", "Cryptographic primitives",
        "Imports '{mod}' (verify cert handling)"),
    ("secrets",      "LOW", "Cryptographic primitives",
        "Imports '{mod}' (CSPRNG)"),
    ("crypt",        "LOW", "Cryptographic primitives",
        "Imports '{mod}' (legacy crypt(3); deprecated)"),

    ("cgi",          "LOW", "Deprecated module",
        "Imports '{mod}' (deprecated)"),
    ("cgitb",        "LOW", "Deprecated module",
        "Imports '{mod}' (deprecated)"),
    ("optparse",     "LOW", "Deprecated module",
        "Imports '{mod}' (superseded by argparse)"),
]


# HIGH calls: always dangerous regardless of arguments.
DYNAMIC_EXEC_CALLS = {"eval", "exec", "compile", "__import__"}

OS_SHELL_CALLS = {
    "os.system", "os.popen",
    "os.execv",  "os.execve", "os.execvp", "os.execvpe",
    "os.execlp", "os.execlpe",
    "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnlpe",
    "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe",
}

# These wrap a shell unconditionally (no shell= kwarg to check).
ALWAYS_SHELL_SUBPROCESS = {
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
}

# These are HIGH only when called with shell=True.
SHELL_OPTIONAL_SUBPROCESS = {
    "subprocess.run", "subprocess.call",
    "subprocess.check_call", "subprocess.check_output",
    "subprocess.Popen",
}

UNSAFE_DESERIALIZE_CALLS = {
    "pickle.loads",  "pickle.load",
    "cPickle.loads", "cPickle.load",
    "marshal.loads", "marshal.load",
}

# MEDIUM: only when called with a non-literal attribute name.
REFLECTION_CALLS = {"getattr", "setattr", "delattr", "vars"}

# LOW/MEDIUM: only when the call mentions a sensitive keyword.
LOGGING_CALLS = {
    "print",
    "logging.info", "logging.debug", "logging.warning",
    "logging.error", "logging.critical",
}

SENSITIVE_KEYWORDS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "auth", "credential", "private_key", "bearer",
)


# ============================================================
# AST HELPERS
# ============================================================

def _call_name(node: ast.Call) -> Optional[str]:
    """
    Resolve 'foo' / 'foo.bar' / 'foo.bar.baz' from a Call node.
    Returns None for callables we can't statically name.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts: List[str] = []
        cur: ast.AST = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    return None


def _import_module_names(node) -> List[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module or ""]
    return []


# ============================================================
# VISITOR
# ============================================================

class _SecurityVisitor(ast.NodeVisitor):

    def __init__(self, rel_path: str):
        self.rel_path = rel_path
        self.findings: List[Finding] = []

    # ---- imports --------------------------------------------

    def visit_Import(self, node):
        self._check_imports(node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        self._check_imports(node)
        self.generic_visit(node)

    def _check_imports(self, node):
        for mod in _import_module_names(node):
            if not mod:
                continue
            for prefix, sev, cat, tpl in IMPORT_RULES:
                if mod == prefix or mod.startswith(prefix + "."):
                    self._emit(node, sev, cat, tpl.format(mod=mod))
                    break

    # ---- calls ----------------------------------------------

    def visit_Call(self, node):
        name = _call_name(node)
        if name:
            self._check_call(node, name)
        self.generic_visit(node)

    def _check_call(self, node: ast.Call, name: str):
        if name in DYNAMIC_EXEC_CALLS:
            self._emit(node, "HIGH", "Dynamic code execution",
                       f"Call to '{name}' executes arbitrary code")
            return

        if name in OS_SHELL_CALLS:
            self._emit(node, "HIGH", "Shell execution",
                       f"Call to '{name}' executes a shell command")
            return

        if name in ALWAYS_SHELL_SUBPROCESS:
            self._emit(node, "HIGH", "Shell execution",
                       f"Call to '{name}' always invokes a shell")
            return

        if name in SHELL_OPTIONAL_SUBPROCESS and self._has_shell_true(node):
            self._emit(node, "HIGH", "Shell injection vector",
                       f"'{name}' called with shell=True is a shell-injection risk")
            return

        if name in UNSAFE_DESERIALIZE_CALLS:
            self._emit(node, "HIGH", "Unsafe deserialization",
                       f"'{name}' deserialises untrusted data (RCE risk)")
            return

        if name == "yaml.load" and self._yaml_load_unsafe(node):
            self._emit(node, "HIGH", "Unsafe deserialization",
                       "yaml.load() without Loader= is unsafe (use yaml.safe_load)")
            return

        if name in REFLECTION_CALLS and self._has_dynamic_name_arg(node):
            self._emit(node, "MEDIUM", "Dynamic reflection",
                       f"'{name}' called with a non-literal attribute name")
            return

        if name in LOGGING_CALLS and self._contains_sensitive_keyword(node):
            self._emit(node, "MEDIUM", "Potential secret logging",
                       f"'{name}' argument mentions a sensitive keyword "
                       f"(password/token/key)")
            return

    # ---- semantic predicates --------------------------------

    @staticmethod
    def _has_shell_true(call: ast.Call) -> bool:
        for kw in call.keywords:
            if kw.arg == "shell" \
                    and isinstance(kw.value, ast.Constant) \
                    and kw.value.value is True:
                return True
        return False

    @staticmethod
    def _yaml_load_unsafe(call: ast.Call) -> bool:
        # Loader can be positional (yaml.load(data, SafeLoader)) or kw
        # (yaml.load(data, Loader=SafeLoader)). Missing entirely => unsafe.
        if not (call.args or call.keywords):
            return False
        if len(call.args) >= 2:
            return False
        for kw in call.keywords:
            if kw.arg == "Loader":
                return False
        return True

    @staticmethod
    def _has_dynamic_name_arg(call: ast.Call) -> bool:
        if len(call.args) < 2:
            return False
        name_arg = call.args[1]
        if isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str):
            return False
        return True

    @staticmethod
    def _contains_sensitive_keyword(call: ast.Call) -> bool:
        try:
            src = ast.unparse(call)
        except Exception:
            return False
        src_lower = src.lower()
        return any(kw in src_lower for kw in SENSITIVE_KEYWORDS)

    # ---- emit -----------------------------------------------

    def _emit(self, node, severity: str, category: str, detail: str):
        self.findings.append(Finding(
            severity=severity,
            category=category,
            detail=detail,
            file=self.rel_path,
            line=getattr(node, "lineno", 0),
        ))


# ============================================================
# FILE / REPO SCANNING
# ============================================================

def _scan_file(file_path: Path, repo_root: Path) -> List[Finding]:
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree   = ast.parse(source, filename=str(file_path))
    except (SyntaxError, ValueError):
        return []

    rel = str(file_path.relative_to(repo_root))
    visitor = _SecurityVisitor(rel_path=rel)
    visitor.visit(tree)
    return visitor.findings


def scan_repo(repo_dir: Path) -> RepoReport:
    report = RepoReport(repo_name=repo_dir.name)
    for root, _, files in os.walk(repo_dir):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = Path(root) / fname
            report.findings.extend(_scan_file(fpath, repo_dir))

    report.findings.sort(key=lambda f: (
        SEVERITY_ORDER.get(f.severity, 99),
        f.file,
        f.line,
    ))
    return report


def scan_all_repos(tools_dir: Path) -> List[RepoReport]:
    reports: List[RepoReport] = []
    for repo_dir in sorted(tools_dir.iterdir()):
        if not repo_dir.is_dir() or repo_dir.name.startswith("."):
            continue
        reports.append(scan_repo(repo_dir))
    return reports


# ============================================================
# REPORT RENDERING (plain ASCII)
# ============================================================

def render_report_text(reports: List[RepoReport]) -> str:
    """
    Plain-ASCII render. No box-drawing chars or emoji, so alignment can't
    drift across terminals that size Unicode glyphs differently.
    """
    sep_outer = "=" * 70
    sep_inner = "-" * 70

    lines: List[str] = []
    lines.append(sep_outer)
    lines.append(" TOOLSTOREPY -- SECURITY SCAN REPORT")
    lines.append(f" Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(sep_outer)
    lines.append("")

    total_high = total_med = total_low = 0

    for rep in reports:
        total_high += rep.high_count
        total_med  += rep.medium_count
        total_low  += rep.low_count

        if rep.is_clean:
            status = "CLEAN"
        elif rep.high_count:
            status = "HIGH risk"
        elif rep.medium_count:
            status = "MEDIUM risk"
        else:
            status = "LOW risk"

        lines.append(sep_inner)
        lines.append(f"  Repo:    {rep.repo_name}")
        lines.append(f"  Status:  {status}")
        lines.append(
            f"  Findings -- HIGH: {rep.high_count}  "
            f"MEDIUM: {rep.medium_count}  LOW: {rep.low_count}"
        )
        lines.append(sep_inner)

        if rep.is_clean:
            lines.append("  No issues found.")
            lines.append("")
            continue

        for sev in ("HIGH", "MEDIUM", "LOW"):
            group = [f for f in rep.findings if f.severity == sev]
            if not group:
                continue
            lines.append(f"  [{sev}]")
            for f in group:
                lines.append(f"    {f.file}:{f.line}  ({f.category})")
                lines.append(f"      -> {f.detail}")
            lines.append("")

    lines.append(sep_outer)
    lines.append(" SUMMARY")
    lines.append(sep_outer)
    lines.append(f"  Repos scanned : {len(reports)}")
    lines.append(f"  HIGH          : {total_high}")
    lines.append(f"  MEDIUM        : {total_med}")
    lines.append(f"  LOW           : {total_low}")
    lines.append(f"  Clean repos   : {sum(1 for r in reports if r.is_clean)}")
    lines.append(sep_outer)

    return "\n".join(lines)


# ============================================================
# INTERACTIVE GATING
# ============================================================

def prompt_user_for_risky_repos(
    reports: List[RepoReport],
    interactive: bool = True,
) -> Tuple[List[str], List[str]]:
    """
    For every repo with HIGH findings, ask the user whether to include
    or skip it. With interactive=False (e.g. CI), any repo with HIGH
    findings is auto-skipped.

    Returns:
        allowed_repos : repo names approved for the build
        skipped_repos : repo names excluded from the build
    """
    risky = [r for r in reports if r.high_count > 0]

    if not risky:
        return [r.repo_name for r in reports], []

    if not interactive:
        allowed = [r.repo_name for r in reports if r.high_count == 0]
        skipped = [r.repo_name for r in reports if r.high_count > 0]
        return allowed, skipped

    allowed: List[str] = []
    skipped: List[str] = []

    print()
    print("-" * 68)
    print("  HIGH-SEVERITY FINDINGS REQUIRE YOUR DECISION")
    print("-" * 68)

    for rep in reports:
        if rep.high_count == 0:
            allowed.append(rep.repo_name)
            continue

        print()
        print(f"  Repo: {rep.repo_name}")
        print(f"  HIGH findings: {rep.high_count}")
        for f in rep.findings:
            if f.severity == "HIGH":
                print(f"    - {f.file}:{f.line}  ({f.category})")
                print(f"        -> {f.detail}")

        print()
        while True:
            try:
                ans = input(
                    f"  Include '{rep.repo_name}' despite HIGH findings? "
                    f"[y/N]: "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
                print()

            if ans in ("y", "yes"):
                allowed.append(rep.repo_name)
                print(f"  -> '{rep.repo_name}' included.")
                break
            elif ans in ("n", "no", ""):
                skipped.append(rep.repo_name)
                print(f"  -> '{rep.repo_name}' skipped.")
                break
            else:
                print("  Please enter y or n.")

    return allowed, skipped