import os
import ast
from pathlib import Path
from typing import Dict, List, Set


class ToolParser:
    """
    Parses Python files inside tool repositories and extracts:

        - Structured imports (with alias preservation)
        - Utility blocks
        - @tool-decorated functions
        - Conflict information
    """

    def __init__(self, tools_dir: Path):
        self.tools_dir = Path(tools_dir)

    # ==================================================
    # PUBLIC ENTRYPOINT
    # ==================================================

    def parse_all(self) -> Dict:

        structured_imports = {
            "import": [],
            "from": []
        }

        utilities: List[str] = []
        tools: List[Dict] = []

        seen_tool_names: Set[str] = set()

        conflicts = {
            "duplicate_tools": [],
            "relative_imports": []
        }

        for file_path in self._get_py_files():
            code = self._read_code(file_path)
            tree = ast.parse(code)

            for node in tree.body:

                # --------------------------
                # IMPORTS
                # --------------------------

                if isinstance(node, ast.Import):
                    for alias in node.names:
                        structured_imports["import"].append({
                            "module": alias.name,
                            "alias": alias.asname
                        })

                elif isinstance(node, ast.ImportFrom):

                    # Detect relative import
                    if node.level and node.level > 0:
                        conflicts["relative_imports"].append(
                            ast.get_source_segment(code, node)
                        )
                        continue

                    for alias in node.names:
                        structured_imports["from"].append({
                            "module": node.module,
                            "name": alias.name,
                            "alias": alias.asname
                        })

                # --------------------------
                # FUNCTIONS
                # --------------------------

                elif isinstance(node, ast.FunctionDef):

                    if self._is_tool_function(node):

                        if node.name in seen_tool_names:
                            conflicts["duplicate_tools"].append(node.name)
                        else:
                            seen_tool_names.add(node.name)

                        tools.append({
                            "name": node.name,
                            "source": self._extract_function_with_decorators(node, code),
                            "file": str(file_path)
                        })

                    else:
                        block = ast.get_source_segment(code, node)
                        if block:
                            utilities.append(block)

                # --------------------------
                # CLASSES
                # --------------------------

                elif isinstance(node, ast.ClassDef):
                    block = ast.get_source_segment(code, node)
                    if block:
                        utilities.append(block)

                # --------------------------
                # GLOBAL ASSIGNMENTS
                # --------------------------

                elif isinstance(node, ast.Assign):
                    src = ast.get_source_segment(code, node) or ""
                    if "FastMCP" not in src:
                        utilities.append(src)

        return {
            "imports": structured_imports,
            "utilities": utilities,
            "tools": tools,
            "conflicts": conflicts
        }

    # ==================================================
    # FILE DISCOVERY
    # ==================================================

    def _get_py_files(self) -> List[Path]:
        py_files = []
        for root, _, files in os.walk(self.tools_dir):
            for file in files:
                if file.endswith(".py"):
                    py_files.append(Path(root) / file)
        return py_files

    def _read_code(self, file_path: Path) -> str:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    # ==================================================
    # TOOL DECORATOR DETECTION
    # ==================================================

    def _is_tool_function(self, node: ast.FunctionDef) -> bool:
        for decorator in node.decorator_list:

            # @tool
            if isinstance(decorator, ast.Name) and decorator.id == "tool":
                return True

            # @mcp.tool
            if isinstance(decorator, ast.Attribute) and decorator.attr == "tool":
                return True

            # @tool(...)
            if isinstance(decorator, ast.Call):
                if isinstance(decorator.func, ast.Name) and decorator.func.id == "tool":
                    return True

                if isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "tool":
                    return True

        return False

    # ==================================================
    # FUNCTION SOURCE EXTRACTION
    # ==================================================

    def _extract_function_with_decorators(self, node: ast.FunctionDef, code: str) -> str:

        lines = code.split("\n")
        start_line = node.lineno - 1

        decorators = []
        idx = start_line - 1

        while idx >= 0 and lines[idx].strip().startswith("@"):
            decorators.append(lines[idx])
            idx -= 1

        decorators.reverse()

        func_block = decorators + lines[start_line:node.end_lineno]
        return "\n".join(func_block)
