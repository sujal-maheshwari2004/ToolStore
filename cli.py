import argparse
import sys
import json
import traceback
from pathlib import Path

from .orchestrator import ToolStorePy


def main():
    parser = argparse.ArgumentParser(
        prog="toolstorepy",
        description="ToolStorePy - Automatic MCP Tool Builder",
    )
    subparsers = parser.add_subparsers(dest="command")

    _add_build_parser(subparsers)
    cache_parser = _add_cache_parser(subparsers)

    args = parser.parse_args()

    if args.command == "build":
        _handle_build(args)
    elif args.command == "cache":
        _handle_cache(args, cache_parser)
    else:
        parser.print_help()


# --------------------------------------------------
# ARGUMENT DEFINITIONS
# --------------------------------------------------

def _add_build_parser(subparsers):
    p = subparsers.add_parser(
        "build",
        help="Build unified MCP server from tool index and queries",
    )
    p.add_argument("--queries",   required=True, help="Path to queries.json file")
    p.add_argument("--index",     help="Name of built-in tool index")
    p.add_argument("--index-url", help="Direct URL to a downloadable vector index archive")
    p.add_argument("--workspace", default="toolstorepy_workspace",
                   help="Workspace directory (default: toolstorepy_workspace)")
    p.add_argument("--install-requirements", action="store_true",
                   help="Install requirements.txt from cloned repositories")
    p.add_argument("--host", default="0.0.0.0",
                   help="Host the MCP server binds on (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=8000,
                   help="Port the MCP server listens on (default: 8000)")
    p.add_argument("--force-refresh", action="store_true",
                   help="Force re-download of index archive")
    p.add_argument("--verbose", action="store_true",
                   help="Verbose logging, plus full traceback on failure")


def _add_cache_parser(subparsers):
    cache_parser = subparsers.add_parser("cache", help="Manage local repo cache")
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command")

    pop = cache_subparsers.add_parser(
        "populate",
        help="Cache repos from a queries file or a list of URLs",
        description=(
            "Pre-warm the bare-repo cache. Provide either --queries pointing "
            "at a JSON file where each item has a 'git_link' field, OR one or "
            "more --url flags with direct git URLs. A queries file containing "
            "only 'tool_description' fields cannot be used here -- run "
            "'toolstorepy build' first to resolve descriptions to git URLs."
        ),
    )
    src = pop.add_mutually_exclusive_group(required=True)
    src.add_argument("--queries", help="JSON file of items with a 'git_link' field")
    src.add_argument("--url", action="append",
                     help="Direct git URL (can be repeated)")
    pop.add_argument("--force", action="store_true",
                     help="Re-cache repos that are already cached")

    cache_subparsers.add_parser("list",  help="List all cached repos")
    cache_subparsers.add_parser("clear", help="Clear all cached repos")

    return cache_parser


# --------------------------------------------------
# HANDLERS
# --------------------------------------------------

def _handle_build(args):
    if not args.index and not args.index_url:
        print("Error: You must provide either --index or --index-url.", file=sys.stderr)
        sys.exit(1)
    if args.index and args.index_url:
        print("Error: Provide either --index or --index-url, not both.", file=sys.stderr)
        sys.exit(1)

    try:
        toolstore = ToolStorePy(
            workspace=args.workspace,
            install_requirements=args.install_requirements,
            host=args.host,
            port=args.port,
            verbose=args.verbose,
        )
        output_path = toolstore.build(
            queries=args.queries,
            index=args.index,
            index_url=args.index_url,
            force_refresh=args.force_refresh,
        )
        print(f"\nMCP server generated at: {output_path}")
    except Exception as exc:
        if args.verbose:
            traceback.print_exc()
        print(f"\nBuild failed: {exc}", file=sys.stderr)
        sys.exit(1)


def _handle_cache(args, cache_parser):
    from .loader.cache import RepoCache
    repo_cache = RepoCache()

    if args.cache_command == "populate":
        urls = _resolve_populate_urls(args)
        if not urls:
            print("Error: No URLs to cache (empty input).", file=sys.stderr)
            sys.exit(1)
        print(f"Caching {len(urls)} repo(s)...")
        failed = repo_cache.populate_many(urls, force=args.force)
        if failed:
            print(
                f"\nFailed to cache {len(failed)} repo(s):",
                file=sys.stderr,
            )
            for url in failed:
                print(f"  - {url}", file=sys.stderr)
            sys.exit(1)
        print("Done.")

    elif args.cache_command == "list":
        cached = repo_cache.list_cached()
        print(f"Cached repos ({len(cached)}):")
        for name in sorted(cached):
            print(f"  {name}")

    elif args.cache_command == "clear":
        try:
            confirm = input("Clear all cached repos? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            print("Aborted.")
            return
        if confirm in ("y", "yes"):
            repo_cache.clear()
            print("Cache cleared.")
        else:
            print("Aborted.")

    else:
        cache_parser.print_help()


def _resolve_populate_urls(args) -> list:
    """Resolve the URL list for `cache populate` from either --url or --queries."""
    if args.url:
        # De-dupe while preserving first-seen order.
        seen, ordered = set(), []
        for u in args.url:
            if u and u not in seen:
                seen.add(u)
                ordered.append(u)
        return ordered

    queries_path = Path(args.queries)
    if not queries_path.exists():
        print(f"Error: Queries file not found: {args.queries}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(queries_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"Error: Queries file is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"Error: Could not read queries file: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, list):
        print(
            f"Error: Queries file must be a JSON list, got "
            f"{type(data).__name__}.",
            file=sys.stderr,
        )
        sys.exit(1)

    urls, missing = [], 0
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            print(
                f"Error: Item #{i} in queries file is not an object.",
                file=sys.stderr,
            )
            sys.exit(1)
        link = item.get("git_link")
        if isinstance(link, str) and link.strip():
            urls.append(link.strip())
        else:
            missing += 1

    if not urls:
        print(
            "Error: No 'git_link' fields found in the queries file.\n"
            "       'cache populate --queries FILE' expects pre-resolved URLs.\n"
            "       To cache from a description-only queries.json, run\n"
            "       'toolstorepy build' first (which resolves descriptions to\n"
            "       URLs and caches as it goes), or pass URLs directly via --url.",
            file=sys.stderr,
        )
        sys.exit(1)

    if missing:
        print(
            f"Warning: {missing} item(s) in queries file had no 'git_link' "
            f"and were skipped.",
            file=sys.stderr,
        )

    # De-dupe.
    seen, ordered = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered


if __name__ == "__main__":
    main()