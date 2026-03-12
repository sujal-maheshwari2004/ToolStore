"""
utils/security_scanner.py

Static AST-based security scanner for cloned tool repositories.

Checks for:
  HIGH
    - Shell/subprocess execution (subprocess, os.system, os.popen, commands)
    - Dynamic code execution (eval, exec, compile, __import__)
    - Network access (requests, urllib, httpx, aiohttp, socket, ftplib, smtplib)
    - Dangerous pickle / deserialisation (pickle, marshal, shelve, yaml.load)

  MEDIUM
    - File system access (open, os.open, pathlib, shutil, fileinput, tempfile)
    - Environment variable access (os.environ, os.getenv, os.putenv)
    - Reflection / introspection (getattr, setattr, delattr, vars, dir, globals, locals)
    - XML with known insecure parsers (xml.etree, xml.sax, xml.dom, lxml with no defuse)

  LOW
    - Cryptographic primitives used directly (hashlib, hmac, ssl, secrets)
    - Logging of potentially sensitive data (logging.*, print with password/token/secret)
    - Use of deprecated / known-weak modules (cgi, cgitb, imaplib, telnetlib)
"""

import ast
import os
import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────

SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


@dataclass
class Finding:
    severity: str          # HIGH | MEDIUM | LOW
    category: str          # human-readable category name
    detail:   str          # what exactly was detected
    file:     str          # relative path inside repo
    line:     int


@dataclass
class RepoReport:
    repo_name: str
    findings:  list[Finding] = field(default_factory=list)

    @property
    def high_count(self)   -> int: return sum(1 for f in self.findings if f.severity == "HIGH")
    @property
    def medium_count(self) -> int: return sum(1 for f in self.findings if f.severity == "MEDIUM")
    @property
    def low_count(self)    -> int: return sum(1 for f in self.findings if f.severity == "LOW")
    @property
    def is_clean(self)     -> bool: return len(self.findings) == 0


# ─────────────────────────────────────────────────────────────
# RULE DEFINITIONS
# ─────────────────────────────────────────────────────────────

# Each rule is a dict describing what to match and what to report.
# Matched against import statements AND call expressions.

# import-level rules: flag whenever any of these modules are imported
IMPORT_RULES: list[dict] = [
    # HIGH
    dict(severity="HIGH",   category="Shell execution",
         modules={"subprocess", "commands"},
         detail="Module '{mod}' allows shell/process execution"),
    dict(severity="HIGH",   category="Network access",
         modules={"requests", "httpx", "aiohttp", "urllib", "urllib2",
                  "urllib3", "http.client", "ftplib", "smtplib", "socket",
                  "socketserver", "imaplib", "poplib", "nntplib", "telnetlib"},
         detail="Module '{mod}' enables outbound network requests"),
    dict(severity="HIGH",   category="Unsafe deserialisation",
         modules={"pickle", "cPickle", "marshal", "shelve"},
         detail="Module '{mod}' can deserialise arbitrary objects (RCE risk)"),
    # MEDIUM
    dict(severity="MEDIUM", category="File system access",
         modules={"shutil", "fileinput", "glob", "fnmatch", "tempfile"},
         detail="Module '{mod}' provides file system access"),
    dict(severity="MEDIUM", category="XML parsing (potentially unsafe)",
         modules={"xml.etree.ElementTree", "xml.sax", "xml.dom",
                  "xml.dom.minidom", "lxml"},
         detail="Module '{mod}' may be vulnerable to XXE/billion-laughs attacks"),
    # LOW
    dict(severity="LOW",    category="Cryptographic primitives",
         modules={"hashlib", "hmac", "ssl", "secrets", "crypt"},
         detail="Module '{mod}' used directly — ensure correct usage"),
    dict(severity="LOW",    category="Deprecated / weak module",
         modules={"cgi", "cgitb", "imaplib", "telnetlib", "optparse"},
         detail="Module '{mod}' is deprecated or known-weak"),
]

# call-level rules: flag specific function/attribute calls
CALL_RULES: list[dict] = [
    # HIGH — dynamic execution
    dict(severity="HIGH",   category="Dynamic code execution",
         calls={"eval", "exec", "compile", "__import__"},
         detail="Call to '{call}' executes arbitrary code"),
    # HIGH — os-level shell
    dict(severity="HIGH",   category="Shell execution",
         calls={"os.system", "os.popen", "os.execv", "os.execve",
                "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnlpe",
                "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe"},
         detail="Call to '{call}' executes a shell command"),
    # HIGH — yaml unsafe load
    dict(severity="HIGH",   category="Unsafe deserialisation",
         calls={"yaml.load"},
         detail="yaml.load() without Loader= is unsafe — use yaml.safe_load()"),
    # MEDIUM — file operations
    dict(severity="MEDIUM", category="File system access",
         calls={"open", "os.open", "os.fdopen", "os.read", "os.write",
                "os.remove", "os.unlink", "os.rmdir", "os.makedirs",
                "os.rename", "os.replace", "os.chmod", "os.chown",
                "pathlib.Path.open", "pathlib.Path.read_text",
                "pathlib.Path.write_text", "pathlib.Path.read_bytes",
                "pathlib.Path.write_bytes", "pathlib.Path.unlink",
                "pathlib.Path.rmdir", "shutil.rmtree", "shutil.copy",
                "shutil.move", "shutil.copytree"},
         detail="Call to '{call}' performs file system I/O"),
    # MEDIUM — env access
    dict(severity="MEDIUM", category="Environment variable access",
         calls={"os.environ", "os.environ.get", "os.getenv",
                "os.putenv", "os.unsetenv"},
         detail="Call to '{call}' reads or modifies environment variables"),
    # MEDIUM — reflection
    dict(severity="MEDIUM", category="Reflection / introspection",
         calls={"getattr", "setattr", "delattr", "vars", "dir",
                "globals", "locals", "type", "isinstance"},
         detail="Call to '{call}' uses Python reflection"),
    # LOW — sensitive keyword in print/log
    dict(severity="LOW",    category="Potential secret logging",
         calls={"print", "logging.info", "logging.debug", "logging.warning",
                "logging.error", "logging.critical"},
         detail="'{call}' may log sensitive data — review surrounding code"),
]

# Sensitive string keywords that upgrade a LOW print/log finding to MEDIUM
SENSITIVE_KEYWORDS = {
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "auth", "credential", "private_key", "private", "bearer",
}


# ─────────────────────────────────────────────────────────────
# AST VISITOR
# ─────────────────────────────────────────────────────────────

def _call_name(node: ast.Call) -> Optional[str]:
    """
    Resolve the callable name from a Call node.
    Handles:  foo()  /  foo.bar()  /  foo.bar.baz()
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        cur = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _import_module_name(node) -> list[str]:
    """Return all module names referenced by an Import or ImportFrom node."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module or ""] 
    return []


class _SecurityVisitor(ast.NodeVisitor):

    def __init__(self, rel_path: str):
        self.rel_path = rel_path
        self.findings: list[Finding] = []

    # ── imports ──────────────────────────────────────────────

    def visit_Import(self, node):
        self._check_imports(node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        self._check_imports(node)
        self.generic_visit(node)

    def _check_imports(self, node):
        for mod in _import_module_name(node):
            for rule in IMPORT_RULES:
                # Match exact module or any sub-module (e.g. urllib.request)
                if any(mod == m or mod.startswith(m + ".") for m in rule["modules"]):
                    self.findings.append(Finding(
                        severity=rule["severity"],
                        category=rule["category"],
                        detail=rule["detail"].format(mod=mod),
                        file=self.rel_path,
                        line=node.lineno,
                    ))
                    break  # one rule match per import line is enough

    # ── calls ─────────────────────────────────────────────────

    def visit_Call(self, node):
        name = _call_name(node)
        if name:
            self._check_call(node, name)
        self.generic_visit(node)

    def _check_call(self, node: ast.Call, name: str):
        for rule in CALL_RULES:
            if name in rule["calls"]:
                severity = rule["severity"]
                detail   = rule["detail"].format(call=name)

                # Upgrade print/log severity if sensitive keyword found nearby
                if rule["category"] == "Potential secret logging":
                    args_src = ast.unparse(node) if hasattr(ast, "unparse") else ""
                    if any(kw in args_src.lower() for kw in SENSITIVE_KEYWORDS):
                        severity = "MEDIUM"
                        detail   = f"'{name}' may log a sensitive value (password/token/key)"

                self.findings.append(Finding(
                    severity=severity,
                    category=rule["category"],
                    detail=detail,
                    file=self.rel_path,
                    line=node.lineno,
                ))
                break  # one rule match per call site


# ─────────────────────────────────────────────────────────────
# FILE / REPO SCANNING
# ─────────────────────────────────────────────────────────────

def _scan_file(file_path: Path, repo_root: Path) -> list[Finding]:
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree   = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []  # unparseable file — skip silently

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

    # Sort findings: HIGH first, then MEDIUM, then LOW, then by file+line
    report.findings.sort(key=lambda f: (
        SEVERITY_ORDER.get(f.severity, 99),
        f.file,
        f.line,
    ))
    return report


def scan_all_repos(tools_dir: Path) -> list[RepoReport]:
    reports = []
    for repo_dir in sorted(tools_dir.iterdir()):
        if repo_dir.is_dir():
            reports.append(scan_repo(repo_dir))
    return reports


# ─────────────────────────────────────────────────────────────
# REPORT RENDERING
# ─────────────────────────────────────────────────────────────

def _severity_icon(s: str) -> str:
    return {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(s, "⚪")


def render_report_text(reports: list[RepoReport]) -> str:
    width = 70
    lines = []

    lines.append("=" * width)
    lines.append(" TOOLSTOREPY — SECURITY SCAN REPORT")
    lines.append(f" Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * width)
    lines.append("")

    total_high = total_med = total_low = 0

    for rep in reports:
        total_high += rep.high_count
        total_med  += rep.medium_count
        total_low  += rep.low_count

        lines.append(f"┌{'─' * (width - 2)}┐")
        status = "✅ CLEAN" if rep.is_clean else (
            "🔴 HIGH RISK" if rep.high_count else
            "🟡 MEDIUM RISK" if rep.medium_count else
            "🟢 LOW RISK"
        )
        lines.append(f"│  Repo : {rep.repo_name:<{width - 11}}│")
        lines.append(f"│  Status: {status:<{width - 12}}│")
        lines.append(f"│  Findings — HIGH: {rep.high_count}  MEDIUM: {rep.medium_count}  LOW: {rep.low_count:<{width - 42}}│")
        lines.append(f"└{'─' * (width - 2)}┘")

        if rep.is_clean:
            lines.append("  No issues found.")
            lines.append("")
            continue

        # Group findings by severity
        for sev in ("HIGH", "MEDIUM", "LOW"):
            group = [f for f in rep.findings if f.severity == sev]
            if not group:
                continue
            lines.append(f"  {_severity_icon(sev)} {sev}")
            for f in group:
                lines.append(f"    [{f.category}]  {f.file}:{f.line}")
                lines.append(f"      → {f.detail}")
            lines.append("")

    lines.append("=" * width)
    lines.append(" SUMMARY")
    lines.append("=" * width)
    lines.append(f"  Repos scanned : {len(reports)}")
    lines.append(f"  🔴 HIGH       : {total_high}")
    lines.append(f"  🟡 MEDIUM     : {total_med}")
    lines.append(f"  🟢 LOW        : {total_low}")
    lines.append(f"  Clean repos   : {sum(1 for r in reports if r.is_clean)}")
    lines.append("=" * width)

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# INTERACTIVE GATING
# ─────────────────────────────────────────────────────────────

def prompt_user_for_risky_repos(
    reports: list[RepoReport],
) -> tuple[list[str], list[str]]:
    """
    For every repo with HIGH findings, ask the user whether to include
    it in the build or skip it.

    Returns:
        allowed_repos : repo names the user approved
        skipped_repos : repo names the user rejected
    """
    allowed = []
    skipped = []

    risky = [r for r in reports if r.high_count > 0]

    if not risky:
        return [r.repo_name for r in reports], []

    print()
    print("  ┌" + "─" * 66 + "┐")
    print("  │  ⚠️  HIGH-SEVERITY FINDINGS REQUIRE YOUR DECISION" + " " * 17 + "│")
    print("  └" + "─" * 66 + "┘")

    for rep in reports:
        if rep.high_count == 0:
            allowed.append(rep.repo_name)
            continue

        print()
        print(f"  🔴  Repo: {rep.repo_name}")
        print(f"      HIGH findings: {rep.high_count}")
        for f in rep.findings:
            if f.severity == "HIGH":
                print(f"        • [{f.category}]  {f.file}:{f.line}")
                print(f"          → {f.detail}")

        print()
        while True:
            try:
                ans = input(
                    f"  Include '{rep.repo_name}' in the build despite HIGH findings? [y/N]: "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
                print()

            if ans in ("y", "yes"):
                allowed.append(rep.repo_name)
                print(f"  ✔  '{rep.repo_name}' included.")
                break
            elif ans in ("n", "no", ""):
                skipped.append(rep.repo_name)
                print(f"  ✖  '{rep.repo_name}' skipped.")
                break
            else:
                print("  Please enter y or n.")

    return allowed, skipped