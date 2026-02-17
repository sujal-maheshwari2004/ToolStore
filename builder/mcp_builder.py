from pathlib import Path
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

    def __init__(self, tools_dir: Path, output_file: Path, verbose: bool = False):
        self.tools_dir = Path(tools_dir)
        self.output_file = Path(output_file)

    # ==================================================
    # PUBLIC ENTRYPOINT
    # ==================================================

    def build(self):

        parser = ToolParser(self.tools_dir)
        parsed = parser.parse_all()

        safe_import_lines = self._build_import_lines(parsed["imports"])

        safe_tools = self._filter_tools(parsed)

        utilities = parsed["utilities"]

        self._write_output(
            imports=safe_import_lines,
            utilities=utilities,
            tools=safe_tools
        )

    # ==================================================
    # TOOL FILTERING
    # ==================================================

    def _filter_tools(self, parsed):

        tools = parsed["tools"]
        conflicts = parsed["conflicts"]

        duplicate_names = set(conflicts["duplicate_tools"])

        seen: Set[str] = set()
        safe_tools = []

        for tool in tools:
            name = tool["name"]

            # Skip duplicates beyond first occurrence
            if name in duplicate_names:
                if name in seen:
                    continue

            seen.add(name)
            safe_tools.append(tool["source"])

        return safe_tools

    # ==================================================
    # IMPORT BUILDING
    # ==================================================

    def _build_import_lines(self, structured_imports):

        import_lines = set()

        # Regular imports
        for item in structured_imports["import"]:
            if item["alias"]:
                import_lines.add(f"import {item['module']} as {item['alias']}")
            else:
                import_lines.add(f"import {item['module']}")

        # From imports
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
    # FILE WRITING
    # ==================================================

    def _write_output(self, imports, utilities, tools):

        with open(self.output_file, "w", encoding="utf-8") as out:

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

