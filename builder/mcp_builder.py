from pathlib import Path
from typing import Optional
from typing import Set
from .parser import ToolParser
import logging

logger = logging.getLogger("ToolStorePy")

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
        allowed_dirs = [
            d for d in self.tools_dir.iterdir()
            if d.is_dir() and d.name not in self.skipped_repos
        ]

        parser = ToolParser(self.tools_dir, allowed_dirs=allowed_dirs)
        parsed = parser.parse_all()

        self._log_conflicts(parsed["conflicts"])

        safe_import_lines = self._build_import_lines(parsed["imports"])
        utilities         = parsed["utilities"]
        tools             = [t["source"] for t in parsed["tools"]]

        self._write_output(
            imports=safe_import_lines,
            utilities=utilities,
            tools=tools,
            conflicts=parsed["conflicts"],
        )

    # ==================================================
    # CONFLICT LOGGING
    # ==================================================

    def _log_conflicts(self, conflicts: dict):
        for original, renamed, fpath in conflicts.get("duplicate_tools", []):
            logger.warning(
                f"[BUILD] @tool '{original}' appears in multiple repos — "
                f"renamed to '{renamed}' (from {fpath})"
            )

        for original, renamed, fpath in conflicts.get("duplicate_helpers", []):
            logger.warning(
                f"[BUILD] Helper function '{original}' collision — "
                f"renamed to '{renamed}' (from {fpath})"
            )

        for original, renamed, fpath in conflicts.get("duplicate_globals", []):
            logger.warning(
                f"[BUILD] Global variable '{original}' collision — "
                f"renamed to '{renamed}' (from {fpath})"
            )

        for src in conflicts.get("relative_imports", []):
            logger.warning(
                f"[BUILD] Relative import dropped (not resolvable outside repo): {src.strip()}"
            )

        for src in conflicts.get("star_imports", []):
            logger.warning(
                f"[BUILD] Star import kept verbatim — may cause name pollution: {src.strip()}"
            )

        for mod, kept, dropped, fpath in conflicts.get("alias_conflicts", []):
            logger.warning(
                f"[BUILD] Import alias conflict for '{mod}': "
                f"keeping alias '{kept}', dropping '{dropped}' (from {fpath})"
            )

        for repo in conflicts.get("empty_repos", []):
            logger.warning(
                f"[BUILD] Repo '{repo}' contributed no @tool functions — "
                f"it will not appear in the generated server."
            )

        for tool_name, symbol, fpath in conflicts.get("tools_with_missing_helpers", []):
            logger.warning(
                f"[BUILD] @tool '{tool_name}' references '{symbol}' which came from "
                f"a relative import that was dropped. This tool may fail at runtime. "
                f"(file: {fpath})"
            )

    # ==================================================
    # IMPORT BUILDING
    # ==================================================

    def _build_import_lines(self, structured_imports: dict) -> list:
        import_lines = set()

        for item in structured_imports["import"]:
            if item["alias"]:
                import_lines.add(f"import {item['module']} as {item['alias']}")
            else:
                import_lines.add(f"import {item['module']}")

        for item in structured_imports["from"]:
            if item["name"] == "*":
                import_lines.add(f"from {item['module']} import *")
            elif item["alias"]:
                import_lines.add(
                    f"from {item['module']} import {item['name']} as {item['alias']}"
                )
            else:
                import_lines.add(
                    f"from {item['module']} import {item['name']}"
                )

        return sorted(import_lines)

    # ==================================================
    # COMMENT BLOCKS
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

    def _build_conflicts_comment_block(self, conflicts: dict) -> str:
        """
        Emit a comment block into the generated server summarising every
        rename or warning that was applied during the build, so the developer
        can audit the output without reading the build log.
        """
        sections = []

        if conflicts.get("duplicate_tools"):
            sections.append("# @tool renames (duplicate tool names across repos):")
            for orig, new, fpath in conflicts["duplicate_tools"]:
                sections.append(f"#   {orig!r} → {new!r}  ({Path(fpath).name})")

        if conflicts.get("duplicate_helpers"):
            sections.append("# Helper function renames (collision across repos):")
            for orig, new, fpath in conflicts["duplicate_helpers"]:
                sections.append(f"#   {orig!r} → {new!r}  ({Path(fpath).name})")

        if conflicts.get("duplicate_globals"):
            sections.append("# Global variable renames (collision across repos):")
            for orig, new, fpath in conflicts["duplicate_globals"]:
                sections.append(f"#   {orig!r} → {new!r}  ({Path(fpath).name})")

        if conflicts.get("relative_imports"):
            sections.append("# Relative imports dropped (cannot be resolved outside their repo):")
            for src in conflicts["relative_imports"]:
                sections.append(f"#   {src.strip()}")

        if conflicts.get("star_imports"):
            sections.append("# Star imports kept verbatim (review for name pollution):")
            for src in conflicts["star_imports"]:
                sections.append(f"#   {src.strip()}")

        if conflicts.get("alias_conflicts"):
            sections.append("# Import alias conflicts (first alias kept):")
            for mod, kept, dropped, fpath in conflicts["alias_conflicts"]:
                sections.append(
                    f"#   {mod!r}: kept alias {kept!r}, dropped {dropped!r}"
                    f"  ({Path(fpath).name})"
                )

        if conflicts.get("empty_repos"):
            sections.append("# Repos that contributed no @tool functions:")
            for repo in conflicts["empty_repos"]:
                sections.append(f"#   {repo}")

        if conflicts.get("tools_with_missing_helpers"):
            sections.append(
                "# ⚠  Tools referencing symbols from dropped relative imports"
                " (may fail at runtime):"
            )
            for tool_name, sym, fpath in conflicts["tools_with_missing_helpers"]:
                sections.append(
                    f"#   @tool {tool_name!r} references {sym!r}"
                    f"  ({Path(fpath).name})"
                )

        if not sections:
            return ""

        header = [
            "# " + "=" * 58,
            "# 🔍  BUILD NOTES (renames and warnings)",
            "# " + "=" * 58,
        ]
        footer = ["# " + "=" * 58]
        return "\n".join(header + sections + footer) + "\n"

    # ==================================================
    # FILE WRITING
    # ==================================================

    def _write_output(self, imports, utilities, tools, conflicts):
        env_block       = self._build_env_comment_block()
        skipped_block   = self._build_skipped_comment_block()
        conflicts_block = self._build_conflicts_comment_block(conflicts)

        with open(self.output_file, "w", encoding="utf-8") as out:

            if env_block:
                out.write(env_block)
                out.write("\n")

            if skipped_block:
                out.write(skipped_block)
                out.write("\n")

            if conflicts_block:
                out.write(conflicts_block)
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
