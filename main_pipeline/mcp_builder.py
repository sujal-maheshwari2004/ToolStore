import os
import ast

TOOLS_DIR = "../tools"
DEFAULT_OUTPUT = "../mcp_unified_server.py"

HEADER = """from mcp.server.fastmcp import FastMCP
mcp = FastMCP("UtilityTools")
"""

FOOTER = """
if __name__ == "__main__":
    mcp.run(transport="stdio")
"""


# ------------------------------
# FILE DISCOVERY
# ------------------------------
def get_py_file_paths(root_dir):
    py_files = []
    for main_dir, dirs, files in os.walk(root_dir):
        for file in files:
            if file.endswith('.py'):
                py_files.append(os.path.join(main_dir, file))
    return py_files


# ------------------------------
# READ FILE
# ------------------------------
def get_code(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


# ------------------------------
# SAFE IMPORT FILTER
# ------------------------------
def is_safe_import(import_line: str) -> bool:
    """
    Excludes imports that would break the unified MCP server.
    """
    blocked_patterns = [
        "FastMCP",
        "mcp.server",
        "server.fastmcp",
        "from server import mcp",
        "import mcp",
        "from mcp",
        "mcp."
    ]

    return not any(pattern in import_line for pattern in blocked_patterns)


# ------------------------------
# DECORATOR + BLOCK PARSING
# ------------------------------
def parse_strict_sections(code):
    imports = set()
    utility_blocks = []
    tool_blocks = []

    lines = code.split('\n')

    # Collect imports (SAFE)
    for line in lines:
        s = line.strip()
        if (s.startswith("import ") or s.startswith("from ")) and is_safe_import(s):
            imports.add(s)

    tree = ast.parse(code)

    for node in tree.body:
        # functions
        if isinstance(node, ast.FunctionDef):
            is_tool = any(
                isinstance(d, ast.Attribute) and d.attr == 'tool'
                or isinstance(d, ast.Call)
                and getattr(getattr(d.func, 'attr', None), 'lower', lambda: None)() == 'tool'
                for d in node.decorator_list
            )

            if is_tool:
                tool_blocks.append(get_func_with_decorator(node, code))
            else:
                block = ast.get_source_segment(code, node)
                if block:
                    utility_blocks.append(block)

        # classes
        elif isinstance(node, ast.ClassDef):
            block = ast.get_source_segment(code, node)
            if block:
                utility_blocks.append(block)

        # top-level assignments
        elif isinstance(node, ast.Assign):
            src = ast.get_source_segment(code, node) or ""
            if "mcp = FastMCP(" not in src:
                utility_blocks.append(src)

    return imports, utility_blocks, tool_blocks


# ------------------------------
# EXTRACT FUNCTION + DECORATORS
# ------------------------------
def get_func_with_decorator(node, code):
    lines = code.split('\n')
    start_line = node.lineno - 1

    decorators = []
    idx = start_line - 1
    while idx >= 0 and lines[idx].strip().startswith('@'):
        decorators.append(lines[idx])
        idx -= 1

    decorators.reverse()
    func_block = decorators + lines[start_line:node.end_lineno]
    return "\n".join(func_block)


# ------------------------------
# MAIN UNIFIED BUILDER
# ------------------------------
def build_unified_server(
    tools_dir=TOOLS_DIR,
    output_file=DEFAULT_OUTPUT
):
    py_files = get_py_file_paths(tools_dir)

    all_imports = set()
    all_utils = []
    all_tools = []

    for file in py_files:
        code = get_code(file)
        imports, utils, tools = parse_strict_sections(code)
        all_imports.update(imports)
        all_utils.extend(utils)
        all_tools.extend(tools)

    with open(output_file, "w", encoding="utf-8") as out:
        out.write(HEADER)
        out.write("# === IMPORTS ===\n")
        out.write("\n".join(sorted(all_imports)))
        out.write("\n\n# === UTILITIES, HELPERS, CLASSES, GLOBALS ===\n")

        for util in all_utils:
            out.write(util.strip() + "\n\n")

        out.write("# === MCP TOOL FUNCTIONS ===\n")
        for tool in all_tools:
            out.write(tool.strip() + "\n\n")

        out.write(FOOTER)

    return output_file
