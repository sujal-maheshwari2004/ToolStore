import logging
from pathlib import Path
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger("ToolStorePy")


# -------------------------------------------------------
# IO HELPERS
# -------------------------------------------------------

def _read_file_safely(path: Path) -> str:
    """
    Read a text file as UTF-8. If invalid bytes are present, replace them
    and warn -- silent corruption is worse than a visible warning.
    """
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning(
            f"[ENV] Non-UTF-8 bytes in {path}; replaced with U+FFFD."
        )
        return raw.decode("utf-8", errors="replace")


def _split_value_and_comment(rest: str) -> Tuple[str, str]:
    """
    Split the right-hand side of a `KEY=...` line into (value, inline_comment).
    Handles quoted values so `#` inside quotes is preserved rather than
    being treated as a comment delimiter.

    Examples:
        ' bar '                  -> ('bar', '')
        ' bar # note'            -> ('bar', '# note')
        ' "v # 1"'               -> ('"v # 1"', '')
        ' "v # 1" # note'        -> ('"v # 1"', '# note')
        " 'https://x/#frag'"     -> ("'https://x/#frag'", '')
    """
    rest = rest.lstrip()

    if rest.startswith(('"', "'")):
        quote = rest[0]
        end_q = rest.find(quote, 1)
        if end_q != -1:
            value = rest[: end_q + 1]
            tail  = rest[end_q + 1:].lstrip()
            if tail.startswith("#"):
                return value, "# " + tail[1:].strip()
            return value, ""
        # Unclosed quote -- treat the whole remainder as the value.
        return rest.rstrip(), ""

    if " #" in rest:
        val_part, _, comment = rest.partition(" #")
        return val_part.strip(), "# " + comment.strip()
    return rest.strip(), ""


# -------------------------------------------------------
# PARSING
# -------------------------------------------------------

def _parse_env_example(path: Path) -> List[Dict]:
    """
    Parse a .env.example file into a list of entries:
        {"type": "blank"}
        {"type": "comment",  "line": "# some comment"}
        {"type": "key",      "key": "FOO", "value": "bar",
                             "inline_comment": "# optional"}
    """
    entries: List[Dict] = []
    content = _read_file_safely(path)

    for raw in content.splitlines():
        line = raw.rstrip("\r")

        if line.strip() == "":
            entries.append({"type": "blank"})
            continue

        if line.strip().startswith("#"):
            entries.append({"type": "comment", "line": line})
            continue

        if "=" in line:
            key_part, _, rest = line.partition("=")
            value, inline_comment = _split_value_and_comment(rest)
            entries.append({
                "type":           "key",
                "key":            key_part.strip(),
                "value":          value,
                "inline_comment": inline_comment,
            })
            continue

        # Unrecognised non-empty line -- preserve as a comment.
        entries.append({"type": "comment", "line": line})

    return entries


# -------------------------------------------------------
# SCANNING
# -------------------------------------------------------

def scan_env_examples(
    tools_dir: Path,
    skipped_repos: Optional[set] = None,
) -> Dict[str, List[Dict]]:
    """
    Walk tools_dir and return {repo_name: parsed_entries} for every
    .env.example found directly inside a repo root. Skips hidden dirs
    and any repo in `skipped_repos`.
    """
    skipped_repos = set(skipped_repos or [])
    found: Dict[str, List[Dict]] = {}
    for repo_dir in sorted(tools_dir.iterdir()):
        if not repo_dir.is_dir():
            continue
        if repo_dir.name.startswith("."):
            continue
        if repo_dir.name in skipped_repos:
            continue
        env_example = repo_dir / ".env.example"
        if env_example.exists():
            found[repo_dir.name] = _parse_env_example(env_example)
            logger.debug(f"[ENV] Found .env.example in {repo_dir.name}")
    return found


# -------------------------------------------------------
# CONFLICT RESOLUTION
# -------------------------------------------------------

def _resolve_conflict(key: str, candidates: List[Dict]) -> Dict:
    """
    Ask the user to choose between conflicting definitions of `key`.
    Raises RuntimeError on EOF -- non-interactive callers should set
    interactive=False on the merge function instead.
    """
    print(f"\n{'=' * 60}")
    print(f"  Conflict: key '{key}' is defined in multiple repos")
    print(f"{'=' * 60}")
    for i, c in enumerate(candidates, 1):
        comment_str = f"  {c['inline_comment']}" if c["inline_comment"] else ""
        print(f"  [{i}] repo: {c['repo']}")
        print(f"       {key}={c['value']}{comment_str}")
    print(f"  [{len(candidates) + 1}] Enter a custom value")
    print()

    while True:
        try:
            raw = input(f"  Choose [1-{len(candidates) + 1}]: ").strip()
        except EOFError:
            raise RuntimeError(
                f"Cannot prompt for .env.example conflict on key '{key}' "
                f"in a non-interactive context. Re-run interactively, or "
                f"call process_env_examples(..., interactive=False) to "
                f"abort on conflicts instead of looping forever on EOF."
            )

        try:
            choice = int(raw)
        except ValueError:
            print("  Please enter a number.")
            continue

        if 1 <= choice <= len(candidates):
            return candidates[choice - 1]

        if choice == len(candidates) + 1:
            try:
                custom_val = input(f"  Enter value for {key}: ").strip()
                custom_comment = input(
                    "  Enter inline comment (leave blank for none): "
                ).strip()
            except EOFError:
                raise RuntimeError(
                    f"Custom value prompt for '{key}' got EOF."
                )
            return {
                "repo":           "custom",
                "value":          custom_val,
                "inline_comment": f"# {custom_comment}" if custom_comment else "",
            }

        print(f"  Invalid choice. Enter a number between 1 and "
              f"{len(candidates) + 1}.")


# -------------------------------------------------------
# MERGING
# -------------------------------------------------------

def _all_candidates_agree(candidates: List[Dict]) -> bool:
    if len(candidates) <= 1:
        return True
    first_value = candidates[0]["value"]
    return all(c["value"] == first_value for c in candidates)


def merge_env_examples(
    repo_entries: Dict[str, List[Dict]],
    interactive: bool = True,
) -> Tuple[List[str], List[str]]:
    """
    Merge parsed entries from multiple repos into a single ordered list.

    - Identical values across repos are auto-merged (no prompt).
    - Real conflicts: prompt the user when interactive=True; otherwise
      raise RuntimeError naming the key and the disagreeing repos.

    Returns:
        merged_lines : list[str] output lines for the merged file
        all_keys     : list[str] key names included, in output order
    """
    key_candidates: Dict[str, List[Dict]] = {}
    repo_structure: Dict[str, List[Dict]] = {}

    for repo, entries in repo_entries.items():
        pending_comments: List[str] = []
        repo_structure[repo] = []

        for entry in entries:
            if entry["type"] == "blank":
                pending_comments.append("")
            elif entry["type"] == "comment":
                pending_comments.append(entry["line"])
            elif entry["type"] == "key":
                key = entry["key"]
                candidate = {
                    "repo":           repo,
                    "value":          entry["value"],
                    "inline_comment": entry["inline_comment"],
                }
                key_candidates.setdefault(key, []).append(candidate)
                repo_structure[repo].append({
                    "type":    "key_ref",
                    "key":     key,
                    "context": list(pending_comments),
                })
                pending_comments = []

    resolved: Dict[str, Dict] = {}
    for key, candidates in key_candidates.items():
        if len(candidates) == 1:
            resolved[key] = candidates[0]
        elif _all_candidates_agree(candidates):
            repos = ", ".join(c["repo"] for c in candidates)
            logger.debug(
                f"[ENV] Auto-merged '{key}' (identical value across "
                f"{repos})."
            )
            resolved[key] = candidates[0]
        else:
            if interactive:
                resolved[key] = _resolve_conflict(key, candidates)
            else:
                repos = ", ".join(c["repo"] for c in candidates)
                raise RuntimeError(
                    f"Conflicting values for '{key}' across repos "
                    f"({repos}) in non-interactive mode."
                )

    merged_lines: List[str] = []
    all_keys:     List[str] = []
    seen_keys:    set       = set()

    for repo, structure in repo_structure.items():
        merged_lines.append("")
        merged_lines.append("# " + "-" * 54)
        merged_lines.append(f"# Repo: {repo}")
        merged_lines.append("# " + "-" * 54)

        for item in structure:
            key = item["key"]
            if key in seen_keys:
                merged_lines.append(
                    f"# (duplicate '{key}' from {repo} -- skipped)"
                )
                continue

            seen_keys.add(key)
            all_keys.append(key)

            for c in item["context"]:
                merged_lines.append(c)

            r = resolved[key]
            if r["repo"] == "custom":
                merged_lines.append("# ^ value chosen interactively")
            elif r["repo"] != repo:
                merged_lines.append(f"# ^ value chosen from repo: {r['repo']}")

            ic = f"  {r['inline_comment']}" if r["inline_comment"] else ""
            merged_lines.append(f"{key}={r['value']}{ic}")

    return merged_lines, all_keys


# -------------------------------------------------------
# .env VALIDATION
# -------------------------------------------------------

def _load_env_keys(env_path: Path) -> set:
    """Return set of keys defined (non-empty) in an existing .env file."""
    keys: set = set()
    content = _read_file_safely(env_path)
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            if val.strip():
                keys.add(key.strip())
    return keys


def validate_env(workspace: Path, required_keys: List[str]) -> List[str]:
    """
    If workspace/.env exists, return required_keys that are missing or
    empty in it. Returns [] if no .env present.
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
    skipped_repos: Optional[set] = None,
    interactive: bool = True,
) -> Tuple[List[str], List[str]]:
    """
    Full pipeline:
      1. Scan repos for .env.example files (excluding `skipped_repos`).
      2. Merge -- auto-resolve identical values; prompt or raise on real
         conflicts based on `interactive`.
      3. Write workspace/.env.example.
      4. Validate against workspace/.env if present.

    Returns:
        all_keys     : env var names from all included repos
        missing_keys : keys absent/empty in workspace/.env ([] if no .env)
    """
    repo_entries = scan_env_examples(tools_dir, skipped_repos=skipped_repos)

    if not repo_entries:
        return [], []

    repos_with_env = list(repo_entries.keys())
    logger.info(
        f"[ENV] Found .env.example in {len(repos_with_env)} repo(s): "
        + ", ".join(repos_with_env)
    )

    merged_lines, all_keys = merge_env_examples(
        repo_entries, interactive=interactive,
    )

    out_path = workspace / ".env.example"
    header = [
        "# ToolStorePy -- merged .env.example",
        "# Generated from .env.example files found in cloned tool repos.",
        "# Copy this file to .env and fill in the required values.",
        "#",
        f"# Repos contributing secrets: {', '.join(repos_with_env)}",
        "#",
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header + merged_lines) + "\n")

    logger.info(f"[ENV] Merged .env.example written -> {out_path}")

    missing_keys = validate_env(workspace, all_keys)
    return all_keys, missing_keys