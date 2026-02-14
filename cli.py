import argparse
import sys
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

    args = parser.parse_args()

    if args.command == "build":

        # Validate index arguments
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

    else:
        parser.print_help()
