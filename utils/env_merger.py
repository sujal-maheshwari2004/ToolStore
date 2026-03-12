import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ToolStorePy")


# -------------------------------------------------------
# PARSING
# -------------------------------------------------------

def _parse_env_example(path: Path) -> list[dict]:
    """
    Parse a .env.example file into a list of entries.
    Each entry is one of:
        {"type": "blank"}
        {"type": "comment",  "line": "# some comment"}
        {"type": "key",      "key": "FOO", "value": "bar", "inline_comment": "# optional"}
    """
    entries = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")

            if line.strip() == "":
                entries.append({"type": "blank"})
                continue

            if line.strip().startswith("#"):
                entries.append({"type": "comment", "line": line})
                continue

            # KEY=value  # optional inline comment
            if "=" in line:
                key_part, _, rest = line.partition("=")
                key = key_part.strip()
                inline_comment = ""
                if " #" in rest:
                    val_part, _, inline_comment = rest.partition(" #")
                    value = val_part.strip()
                    inline_comment = "# " + inline_comment.strip()
                else:
                    value = rest.strip()
                entries.append({
                    "type":           "key",
                    "key":            key,
                    "value":          value,
                    "inline_comment": inline_comment,
                })
                continue

            # Unrecognised line — treat as comment
            entries.append({"type": "comment", "line": line})

    return entries


# -------------------------------------------------------
# SCANNING
# -------------------------------------------------------

def scan_env_examples(tools_dir: Path) -> dict[str, list[dict]]:
    """
    Walk tools_dir and return {repo_name: parsed_entries} for every
    .env.example found directly inside a repo root.
    """
    found = {}
    for repo_dir in sorted(tools_dir.iterdir()):
        if not repo_dir.is_dir():
            continue
        env_example = repo_dir / ".env.example"
        if env_example.exists():
            found[repo_dir.name] = _parse_env_example(env_example)
            logger.debug(f"[ENV] Found .env.example in {repo_dir.name}")
    return found


# -------------------------------------------------------
# INTERACTIVE CONFLICT RESOLUTION
# -------------------------------------------------------

def _resolve_conflict(key: str, candidates: list[dict]) -> dict:
    """
    Ask the user to choose between conflicting definitions of `key`.
    `candidates` is a list of {"repo", "value", "inline_comment"} dicts.
    Returns the chosen candidate (or a custom one).
    """
    print(f"\n{'='*60}")
    print(f"  Conflict: key '{key}' is defined in multiple repos")
    print(f"{'='*60}")
    for i, c in enumerate(candidates, 1):
        comment_str = f"  {c['inline_comment']}" if c["inline_comment"] else ""
        print(f"  [{i}] repo: {c['repo']}")
        print(f"       {key}={c['value']}{comment_str}")
    print(f"  [{len(candidates)+1}] Enter a custom value")
    print()

    while True:
        try:
            raw = input(f"  Choose [1-{len(candidates)+1}]: ").strip()
            choice = int(raw)
        except (ValueError, EOFError):
            print("  Please enter a number.")
            continue

        if 1 <= choice <= len(candidates):
            return candidates[choice - 1]

        if choice == len(candidates) + 1:
            custom_val = input(f"  Enter value for {key}: ").strip()
            custom_comment = input(f"  Enter inline comment (leave blank for none): ").strip()
            return {
                "repo":           "custom",
                "value":          custom_val,
                "inline_comment": f"# {custom_comment}" if custom_comment else "",
            }

        print(f"  Invalid choice. Enter a number between 1 and {len(candidates)+1}.")


# -------------------------------------------------------
# MERGING
# -------------------------------------------------------

def merge_env_examples(
    repo_entries: dict[str, list[dict]],
) -> tuple[list[dict], list[str]]:
    """
    Merge parsed entries from multiple repos into a single ordered list.

    Strategy:
    - Non-key lines (blanks, comments) are kept per-repo under a repo header.
    - Key entries are deduplicated; conflicts trigger an interactive prompt.

    Returns:
        merged  : list of output lines (strings)
        all_keys: ordered list of all key names included
    """
    # First pass: collect all definitions per key across repos
    key_candidates: dict[str, list[dict]] = {}  # key -> [{repo, value, inline_comment, context_comments}]
    repo_structure: dict[str, list] = {}         # repo -> ordered entries with resolved keys

    for repo, entries in repo_entries.items():
        pending_comments: list[str] = []
        repo_structure[repo] = []

        for entry in entries:
            if entry["type"] == "blank":
                pending_comments.append("")
            elif entry["type"] == "comment":
                pending_comments.append(entry["line"])
            elif entry["type"] == "key":
                key = entry["key"]
                candidate = {
                    "repo":             repo,
                    "value":            entry["value"],
                    "inline_comment":   entry["inline_comment"],
                    "context_comments": list(pending_comments),
                }
                key_candidates.setdefault(key, []).append(candidate)
                repo_structure[repo].append({
                    "type":    "key_ref",
                    "key":     key,
                    "context": list(pending_comments),
                })
                pending_comments = []

    # Second pass: resolve conflicts
    resolved: dict[str, dict] = {}   # key -> chosen candidate
    for key, candidates in key_candidates.items():
        if len(candidates) == 1:
            resolved[key] = candidates[0]
        else:
            resolved[key] = _resolve_conflict(key, candidates)

    # Third pass: build merged output lines
    merged_lines: list[str] = []
    all_keys: list[str] = []
    seen_keys: set[str] = set()

    for repo, structure in repo_structure.items():
        # Repo header
        merged_lines.append("")
        merged_lines.append(f"# {'─'*54}")
        merged_lines.append(f"# Repo: {repo}")
        merged_lines.append(f"# {'─'*54}")

        for item in structure:
            key = item["key"]
            if key in seen_keys:
                merged_lines.append(f"# (duplicate '{key}' from {repo} — skipped)")
                continue

            seen_keys.add(key)
            all_keys.append(key)

            # Context comments/blanks
            for c in item["context"]:
                merged_lines.append(c)

            r = resolved[key]
            # Attribution comment
            if r["repo"] != repo:
                merged_lines.append(f"# ^ value chosen from repo: {r['repo']}")

            ic = f"  {r['inline_comment']}" if r["inline_comment"] else ""
            merged_lines.append(f"{key}={r['value']}{ic}")

    return merged_lines, all_keys


# -------------------------------------------------------
# .env VALIDATION
# -------------------------------------------------------

def _load_env_keys(env_path: Path) -> set[str]:
    """Return set of keys defined (non-empty) in an existing .env file."""
    keys = set()
    with open(env_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                if val.strip():
                    keys.add(key.strip())
    return keys


def validate_env(
    workspace: Path,
    required_keys: list[str],
) -> list[str]:
    """
    If workspace/.env exists, return list of required_keys that are
    missing or empty in it.  Returns [] if no .env present (no complaint).
    """
    env_path = workspace / ".env"
    if not env_path.exists():
        return []

    existing = _load_env_keys(env_path)
    return [k for k in required_keys if k not in existing]


# -------------------------------------------------------
# TOP-LEVEL ENTRY POINT
# -------------------------------------------------------

def process_env_examples(
    tools_dir: Path,
    workspace: Path,
) -> tuple[list[str], list[str]]:
    """
    Full pipeline:
      1. Scan repos for .env.example files
      2. Merge (with interactive conflict resolution)
      3. Write workspace/.env.example
      4. Validate against workspace/.env if it exists

    Returns:
        all_keys     : list of env var names found across all repos
        missing_keys : keys absent/empty in workspace/.env ([] if no .env)
    """
    repo_entries = scan_env_examples(tools_dir)

    if not repo_entries:
        return [], []

    repos_with_env = list(repo_entries.keys())
    logger.info(
        f"[ENV] Found .env.example in {len(repos_with_env)} repo(s): "
        + ", ".join(repos_with_env)
    )

    merged_lines, all_keys = merge_env_examples(repo_entries)

    # Write merged .env.example
    out_path = workspace / ".env.example"
    header = [
        "# ToolStorePy — merged .env.example",
        "# Generated from .env.example files found in cloned tool repos.",
        "# Copy this file to .env and fill in the required values.",
        "#",
        f"# Repos contributing secrets: {', '.join(repos_with_env)}",
        "#",
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header + merged_lines) + "\n")

    logger.info(f"[ENV] Merged .env.example written → {out_path}")

    missing_keys = validate_env(workspace, all_keys)

    return all_keys, missing_keys