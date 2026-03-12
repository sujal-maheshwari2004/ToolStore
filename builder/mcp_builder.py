from pathlib import Path
from typing import Optional
from typing import Set
from .parser import ToolParser


HEADER = """from mcp.server.fastmcp import FastMCP
mcp = FastMCP("UtilityTools")
"""

FOOTER = """
if __name__ == "__main__":
    mcp.run(transport="streamable-http")
"""


class MCPBuilder:

    def __init__(
        self,
        tools_dir: Path,
        output_file: Path,
        env_keys: Optional[list] = None,
        skipped_repos: Optional[list] = None,
        verbose: bool = False,
    ):
        self.tools_dir     = Path(tools_dir)
        self.output_file   = Path(output_file)
        self.env_keys      = env_keys or []
        self.skipped_repos = set(skipped_repos or [])

    # ==================================================
    # PUBLIC ENTRYPOINT
    # ==================================================

    def build(self):
        # Only parse repos that were not skipped by the user
        allowed_dirs = [
            d for d in self.tools_dir.iterdir()
            if d.is_dir() and d.name not in self.skipped_repos
        ]

        parser = ToolParser(self.tools_dir, allowed_dirs=allowed_dirs)
        parsed = parser.parse_all()

        safe_import_lines = self._build_import_lines(parsed["imports"])
        safe_tools        = self._filter_tools(parsed)
        utilities         = parsed["utilities"]

        self._write_output(
            imports=safe_import_lines,
            utilities=utilities,
            tools=safe_tools,
        )

    # ==================================================
    # TOOL FILTERING
    # ==================================================

    def _filter_tools(self, parsed):
        tools          = parsed["tools"]
        duplicate_names = set(parsed["conflicts"]["duplicate_tools"])

        seen: Set[str] = set()
        safe_tools = []

        for tool in tools:
            name = tool["name"]
            if name in duplicate_names and name in seen:
                continue
            seen.add(name)
            safe_tools.append(tool["source"])

        return safe_tools

    # ==================================================
    # IMPORT BUILDING
    # ==================================================

    def _build_import_lines(self, structured_imports):
        import_lines = set()

        for item in structured_imports["import"]:
            if item["alias"]:
                import_lines.add(f"import {item['module']} as {item['alias']}")
            else:
                import_lines.add(f"import {item['module']}")

        for item in structured_imports["from"]:
            if item["alias"]:
                import_lines.add(
                    f"from {item['module']} import {item['name']} as {item['alias']}"
                )
            else:
                import_lines.add(
                    f"from {item['module']} import {item['name']}"
                )

        return sorted(import_lines)

    # ==================================================
    # ENV COMMENT BLOCK
    # ==================================================

    def _build_env_comment_block(self) -> str:
        if not self.env_keys:
            return ""
        lines = [
            "# " + "=" * 58,
            "# ⚠️  REQUIRED ENVIRONMENT VARIABLES",
            "# " + "=" * 58,
            "# One or more tools in this server require secrets.",
            "# Copy workspace/.env.example → workspace/.env and fill",
            "# in the values before running this server.",
            "#",
            "# Required keys:",
        ]
        for key in self.env_keys:
            lines.append(f"#   • {key}")
        lines.append("# " + "=" * 58)
        return "\n".join(lines) + "\n"

    # ==================================================
    # SKIPPED REPOS COMMENT BLOCK
    # ==================================================

    def _build_skipped_comment_block(self) -> str:
        if not self.skipped_repos:
            return ""
        lines = [
            "# " + "=" * 58,
            "# ✖  REPOS EXCLUDED DUE TO HIGH SECURITY FINDINGS",
            "# " + "=" * 58,
            "# The following repos were skipped at your request",
            "# after the pre-build security scan:",
            "#",
        ]
        for repo in sorted(self.skipped_repos):
            lines.append(f"#   • {repo}")
        lines.append("# " + "=" * 58)
        return "\n".join(lines) + "\n"

    # ==================================================
    # FILE WRITING
    # ==================================================

    def _write_output(self, imports, utilities, tools):
        env_block     = self._build_env_comment_block()
        skipped_block = self._build_skipped_comment_block()

        with open(self.output_file, "w", encoding="utf-8") as out:

            if env_block:
                out.write(env_block)
                out.write("\n")

            if skipped_block:
                out.write(skipped_block)
                out.write("\n")

            out.write(HEADER)
            out.write("\n# === IMPORTS ===\n")
            out.write("\n".join(imports))
            out.write("\n\n# === UTILITIES ===\n\n")

            for util in utilities:
                out.write(util.strip() + "\n\n")

            out.write("# === MCP TOOL FUNCTIONS ===\n\n")

            for tool in tools:
                out.write(tool.strip() + "\n\n")

            out.write(FOOTER)