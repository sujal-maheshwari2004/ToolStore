import os
import io
import ast
import logging
from pathlib import Path
from typing import Optional, List, Tuple

from .parser import ToolParser

logger = logging.getLogger("ToolStorePy")


class MCPBuilder:

    def __init__(
        self,
        tools_dir: Path,
        output_file: Path,
        env_keys: Optional[List[str]] = None,
        skipped_repos: Optional[List[str]] = None,
        server_name: str = "UtilityTools",
        transport: str = "streamable-http",
        host: str = "0.0.0.0",
        port: int = 8000,
        verbose: bool = False,
    ):
        self.tools_dir     = Path(tools_dir)
        self.output_file   = Path(output_file)
        self.env_keys      = env_keys or []
        self.skipped_repos = set(skipped_repos or [])
        self.server_name   = server_name
        self.transport     = transport
        self.host          = host
        self.port          = port

    # ==================================================
    # PUBLIC ENTRYPOINT
    # ==================================================

    def build(self):
        allowed_dirs = [
            d for d in self.tools_dir.iterdir()
            if d.is_dir()
            and d.name not in self.skipped_repos
            and not d.name.startswith(".")
        ]

        parser = ToolParser(self.tools_dir, allowed_dirs=allowed_dirs)
        parsed = parser.parse_all()

        self._log_conflicts(parsed["conflicts"])

        if not parsed["tools"]:
            logger.warning(
                "[BUILD] No @tool functions were found in any repo. "
                "The generated server will not expose any tools."
            )

        future_imports, other_imports = self._build_import_lines(parsed["imports"])
        utilities = parsed["utilities"]
        tools     = [t["source"] for t in parsed["tools"]]

        self._write_output(
            future_imports=future_imports,
            imports=other_imports,
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
                f"[BUILD] @tool '{original}' appears in multiple repos -- "
                f"renamed to '{renamed}' (from {fpath})"
            )

        for original, renamed, fpath in conflicts.get("duplicate_helpers", []):
            logger.warning(
                f"[BUILD] Helper function '{original}' collision -- "
                f"renamed to '{renamed}' (from {fpath})"
            )

        for original, renamed, fpath in conflicts.get("duplicate_globals", []):
            logger.warning(
                f"[BUILD] Global variable '{original}' collision -- "
                f"renamed to '{renamed}' (from {fpath})"
            )

        for original, renamed, fpath in conflicts.get("duplicate_classes", []):
            logger.warning(
                f"[BUILD] Class '{original}' collision -- "
                f"renamed to '{renamed}' (from {fpath})"
            )

        for src in conflicts.get("relative_imports", []):
            logger.warning(
                f"[BUILD] Relative import dropped (not resolvable outside repo): "
                f"{src.strip()}"
            )

        for src in conflicts.get("star_imports", []):
            logger.warning(
                f"[BUILD] Star import kept verbatim -- may cause name pollution: "
                f"{src.strip()}"
            )

        for mod, kept, dropped, fpath in conflicts.get("alias_conflicts", []):
            logger.warning(
                f"[BUILD] Import alias conflict for '{mod}': "
                f"keeping alias '{kept}', dropping '{dropped}' (from {fpath})"
            )

        for repo in conflicts.get("empty_repos", []):
            logger.warning(
                f"[BUILD] Repo '{repo}' contributed no @tool functions -- "
                f"it will not appear in the generated server."
            )

        for tool_name, symbol, fpath in conflicts.get("tools_with_missing_helpers", []):
            logger.warning(
                f"[BUILD] @tool '{tool_name}' references '{symbol}' which came from "
                f"a relative import that was dropped. This tool may fail at runtime. "
                f"(file: {fpath})"
            )

        for cls_name, method_name, fpath in conflicts.get("tool_methods_skipped", []):
            logger.warning(
                f"[BUILD] @tool method '{cls_name}.{method_name}' was not extracted "
                f"as a standalone tool (it lives inside a class). "
                f"(file: {fpath})"
            )

        for fpath, error in conflicts.get("unparseable_files", []):
            logger.warning(
                f"[BUILD] File could not be parsed and was skipped: {fpath} ({error})"
            )

        for fpath in conflicts.get("decoding_replacements", []):
            logger.warning(
                f"[BUILD] File contained non-UTF-8 bytes; replacement characters "
                f"were inserted (extracted code may be subtly wrong): {fpath}"
            )

    # ==================================================
    # IMPORT BUILDING
    # ==================================================

    def _build_import_lines(
        self, structured_imports: dict
    ) -> Tuple[List[str], List[str]]:
        """
        Returns (future_imports, other_imports).
        `__future__` imports must be emitted before any other code.
        """
        future_lines: set = set()
        other_lines:  set = set()

        for item in structured_imports["import"]:
            if item["alias"]:
                other_lines.add(f"import {item['module']} as {item['alias']}")
            else:
                other_lines.add(f"import {item['module']}")

        for item in structured_imports["from"]:
            if item["name"] == "*":
                line = f"from {item['module']} import *"
            elif item["alias"]:
                line = (
                    f"from {item['module']} import "
                    f"{item['name']} as {item['alias']}"
                )
            else:
                line = f"from {item['module']} import {item['name']}"

            if item["module"] == "__future__":
                future_lines.add(line)
            else:
                other_lines.add(line)

        return sorted(future_lines), sorted(other_lines)

    # ==================================================
    # COMMENT BLOCKS
    # ==================================================

    def _build_env_comment_block(self) -> str:
        if not self.env_keys:
            return ""
        lines = [
            "# " + "=" * 58,
            "# REQUIRED ENVIRONMENT VARIABLES",
            "# " + "=" * 58,
            "# One or more tools in this server require secrets.",
            "# Copy workspace/.env.example -> workspace/.env and fill",
            "# in the values before running this server.",
            "#",
            "# Required keys:",
        ]
        for key in self.env_keys:
            lines.append(f"#   - {key}")
        lines.append("# " + "=" * 58)
        return "\n".join(lines) + "\n"

    def _build_skipped_comment_block(self) -> str:
        if not self.skipped_repos:
            return ""
        lines = [
            "# " + "=" * 58,
            "# REPOS EXCLUDED DUE TO HIGH SECURITY FINDINGS",
            "# " + "=" * 58,
            "# The following repos were skipped at your request",
            "# after the pre-build security scan:",
            "#",
        ]
        for repo in sorted(self.skipped_repos):
            lines.append(f"#   - {repo}")
        lines.append("# " + "=" * 58)
        return "\n".join(lines) + "\n"

    def _build_conflicts_comment_block(self, conflicts: dict) -> str:
        sections: List[str] = []

        if conflicts.get("duplicate_tools"):
            sections.append("# @tool renames (duplicate tool names across repos):")
            for orig, new, fpath in conflicts["duplicate_tools"]:
                sections.append(f"#   {orig!r} -> {new!r}  ({Path(fpath).name})")

        if conflicts.get("duplicate_helpers"):
            sections.append("# Helper function renames (collision across repos):")
            for orig, new, fpath in conflicts["duplicate_helpers"]:
                sections.append(f"#   {orig!r} -> {new!r}  ({Path(fpath).name})")

        if conflicts.get("duplicate_globals"):
            sections.append("# Global variable renames (collision across repos):")
            for orig, new, fpath in conflicts["duplicate_globals"]:
                sections.append(f"#   {orig!r} -> {new!r}  ({Path(fpath).name})")

        if conflicts.get("duplicate_classes"):
            sections.append("# Class renames (collision across repos):")
            for orig, new, fpath in conflicts["duplicate_classes"]:
                sections.append(f"#   {orig!r} -> {new!r}  ({Path(fpath).name})")

        if conflicts.get("relative_imports"):
            sections.append(
                "# Relative imports dropped (cannot be resolved outside their repo):"
            )
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
                "# WARNING: tools referencing symbols from dropped relative imports "
                "(may fail at runtime):"
            )
            for tool_name, sym, fpath in conflicts["tools_with_missing_helpers"]:
                sections.append(
                    f"#   @tool {tool_name!r} references {sym!r}"
                    f"  ({Path(fpath).name})"
                )

        if conflicts.get("tool_methods_skipped"):
            sections.append(
                "# @tool methods inside classes (not extracted as standalone tools):"
            )
            for cls_name, method_name, fpath in conflicts["tool_methods_skipped"]:
                sections.append(
                    f"#   {cls_name}.{method_name}  ({Path(fpath).name})"
                )

        if conflicts.get("unparseable_files"):
            sections.append("# Files that failed to parse and were skipped:")
            for fpath, error in conflicts["unparseable_files"]:
                sections.append(f"#   {Path(fpath).name}  ({error})")

        if conflicts.get("decoding_replacements"):
            sections.append(
                "# Files with non-UTF-8 bytes (replaced with U+FFFD; "
                "extracted code may be subtly wrong):"
            )
            for fpath in conflicts["decoding_replacements"]:
                sections.append(f"#   {Path(fpath).name}")

        if not sections:
            return ""

        header = [
            "# " + "=" * 58,
            "# BUILD NOTES (renames and warnings)",
            "# " + "=" * 58,
        ]
        footer = ["# " + "=" * 58]
        return "\n".join(header + sections + footer) + "\n"

    # ==================================================
    # FILE WRITING
    # ==================================================

    def _write_output(self, future_imports, imports, utilities, tools, conflicts):
        env_block       = self._build_env_comment_block()
        skipped_block   = self._build_skipped_comment_block()
        conflicts_block = self._build_conflicts_comment_block(conflicts)

        buffer = io.StringIO()

        if env_block:
            buffer.write(env_block)
            buffer.write("\n")

        if skipped_block:
            buffer.write(skipped_block)
            buffer.write("\n")

        if conflicts_block:
            buffer.write(conflicts_block)
            buffer.write("\n")

        # __future__ imports must appear before any other code statement.
        if future_imports:
            buffer.write("\n".join(future_imports))
            buffer.write("\n\n")

        buffer.write("from mcp.server.fastmcp import FastMCP\n")
        buffer.write(f"mcp = FastMCP({self.server_name!r})\n")

        buffer.write("\n# === IMPORTS ===\n")
        if imports:
            buffer.write("\n".join(imports))
        buffer.write("\n\n# === UTILITIES ===\n\n")

        for util in utilities:
            buffer.write(util.strip() + "\n\n")

        buffer.write("# === MCP TOOL FUNCTIONS ===\n\n")

        for tool in tools:
            buffer.write(tool.strip() + "\n\n")

        buffer.write("\n")
        buffer.write('if __name__ == "__main__":\n')
        buffer.write(
            f"    mcp.run(transport={self.transport!r}, "
            f"host={self.host!r}, "
            f"port={self.port!r})\n"
        )

        content = buffer.getvalue()

        # Validate syntax before committing the write.
        try:
            ast.parse(content)
        except SyntaxError as exc:
            logger.error(
                f"[BUILD] Generated server has a syntax error at line "
                f"{exc.lineno}: {exc.msg}. The file will still be written "
                f"so you can inspect it."
            )

        # Atomic write: write to a sibling .tmp file, then rename.
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.output_file.with_name(self.output_file.name + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, self.output_file)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise