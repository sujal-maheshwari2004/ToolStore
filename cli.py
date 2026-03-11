import argparse
import sys
import json
from pathlib import Path

from .orchestrator import ToolStorePy


def main():
    parser = argparse.ArgumentParser(
        prog="toolstorepy",
        description="ToolStorePy - Automatic MCP Tool Builder"
    )

    subparsers = parser.add_subparsers(dest="command")

    # --------------------------------------------------
    # BUILD COMMAND
    # --------------------------------------------------

    build_parser = subparsers.add_parser(
        "build",
        help="Build unified MCP server from tool index and queries"
    )

    build_parser.add_argument(
        "--queries",
        required=True,
        help="Path to queries.json file"
    )

    build_parser.add_argument(
        "--index",
        help="Name of built-in tool index"
    )

    build_parser.add_argument(
        "--index-url",
        help="Direct URL to downloadable vector index archive"
    )

    build_parser.add_argument(
        "--workspace",
        default="toolstorepy_workspace",
        help="Workspace directory (default: toolstorepy_workspace)"
    )

    build_parser.add_argument(
        "--install-requirements",
        action="store_true",
        help="Install requirements.txt from cloned repositories"
    )

    build_parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force re-download of index archive"
    )

    build_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    # --------------------------------------------------
    # CACHE COMMAND
    # --------------------------------------------------

    cache_parser = subparsers.add_parser(
        "cache",
        help="Manage local repo cache"
    )
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command")

    pop_parser = cache_subparsers.add_parser(
        "populate",
        help="Cache repos from a queries.json"
    )
    pop_parser.add_argument(
        "--queries",
        required=True,
        help="Path to queries.json"
    )
    pop_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-cache existing repos"
    )

    cache_subparsers.add_parser("list",  help="List all cached repos")
    cache_subparsers.add_parser("clear", help="Clear all cached repos")

    # --------------------------------------------------
    # PARSE
    # --------------------------------------------------

    args = parser.parse_args()

    # --------------------------------------------------
    # HANDLE BUILD
    # --------------------------------------------------

    if args.command == "build":

        if not args.index and not args.index_url:
            print("Error: You must provide either --index or --index-url.")
            sys.exit(1)

        if args.index and args.index_url:
            print("Error: Provide either --index or --index-url, not both.")
            sys.exit(1)

        try:
            toolstore = ToolStorePy(
                workspace=args.workspace,
                install_requirements=args.install_requirements,
                verbose=args.verbose,
            )

            output_path = toolstore.build(
                queries=args.queries,
                index=args.index,
                index_url=args.index_url,
                force_refresh=args.force_refresh,
            )

            print(f"\nMCP server generated at: {output_path}")

        except Exception as e:
            print(f"\nBuild failed: {e}")
            sys.exit(1)

    # --------------------------------------------------
    # HANDLE CACHE
    # --------------------------------------------------

    elif args.command == "cache":
        from .loader.cache import RepoCache

        repo_cache = RepoCache()

        if args.cache_command == "populate":
            with open(args.queries) as f:
                data = json.load(f)
            urls = list({item["git_link"] for item in data})
            print(f"Caching {len(urls)} repos...")
            repo_cache.populate_many(urls, force=args.force)
            print("Done.")

        elif args.cache_command == "list":
            cached = repo_cache.list_cached()
            print(f"Cached repos ({len(cached)}):")
            for name in sorted(cached):
                print(f"  {name}")

        elif args.cache_command == "clear":
            confirm = input("Clear all cached repos? [y/N]: ")
            if confirm.lower() == "y":
                repo_cache.clear()
                print("Cache cleared.")

        else:
            cache_parser.print_help()

    else:
        parser.print_help()