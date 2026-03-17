import os
import ast
import logging
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple

logger = logging.getLogger("ToolStorePy")

# Directories that are never meant to contribute tool code.
_EXCLUDED_DIRS = {
    "test", "tests", "testing",
    "examples", "example",
    "docs", "doc",
    "scripts",
    "bench", "benchmarks",
    ".git", "__pycache__",
    ".venv", "venv", "env",
    "build", "dist", ".eggs",
}

# Top-level files that should never be parsed.
_EXCLUDED_FILES = {
    "setup.py", "setup.cfg",
    "conftest.py",
}


class ToolParser:
    """
    Parses Python files inside tool repositories and extracts:

        - Structured imports (deduplicated, with alias collision handling)
        - Utility blocks (helper functions + global assignments, renamed on collision)
        - @tool-decorated functions (renamed on collision)
        - Conflict / warning information

    Only directories listed in `allowed_dirs` are parsed.
    If `allowed_dirs` is None, all subdirectories of tools_dir are parsed.

    Edge cases handled
    ------------------
    Import issues
        - Relative imports: dropped but every tool function whose *source text*
          references them is flagged so the caller can warn at build time.
        - Duplicate plain imports (``import os`` twice): collapsed to one.
        - Alias conflicts (``import os`` vs ``import os as operating_system``):
          the first alias seen wins; second is recorded in conflicts.
        - Star imports (``from module import *``): kept verbatim but recorded
          separately so the caller can warn.

    Name collisions
        - Duplicate @tool names: second+ occurrences are renamed
          ``<name>__<repo>`` and a conflict entry is added.
        - Helper function collisions: same rename strategy applied to utilities.
        - Global assignment collisions: same rename strategy (variable is
          prefixed with the repo name).

    File / repo structure issues
        - Test, docs, examples, scripts directories are excluded.
        - setup.py and conftest.py are excluded.
        - if __name__ == '__main__' blocks are skipped cleanly.
        - Repos that contribute zero @tool functions are warned about.
    """

    def __init__(
        self,
        tools_dir: Path,
        allowed_dirs: Optional[List[Path]] = None,
    ):
        self.tools_dir    = Path(tools_dir)
        self.allowed_dirs = (
            [Path(d) for d in allowed_dirs]
            if allowed_dirs is not None
            else None
        )

    # ==================================================
    # PUBLIC ENTRYPOINT
    # ==================================================

    def parse_all(self) -> Dict:
        """
        Returns
        -------
        {
            "imports":   {"import": [...], "from": [...]},
            "utilities": [source_str, ...],
            "tools":     [{"name": str, "source": str, "file": str}, ...],
            "conflicts": {
                "duplicate_tools":    [(original_name, new_name, file), ...],
                "duplicate_helpers":  [(original_name, new_name, file), ...],
                "duplicate_globals":  [(original_name, new_name, file), ...],
                "relative_imports":   [import_source_str, ...],
                "star_imports":       [import_source_str, ...],
                "alias_conflicts":    [(module, kept_alias, dropped_alias, file), ...],
                "empty_repos":        [repo_name, ...],
                "tools_with_missing_helpers": [(tool_name, missing_symbol, file), ...],
            },
        }
        """
        structured_imports: Dict = {"import": [], "from": []}
        utilities:  List[str]  = []
        tools:      List[Dict] = []

        # Deduplication state
        seen_tool_names:    Dict[str, str] = {}   # name -> first file
        seen_helper_names:  Dict[str, str] = {}   # name -> first file
        seen_global_names:  Dict[str, str] = {}   # name -> first file
        seen_plain_imports: Dict[str, Optional[str]] = {}  # module -> alias
        seen_from_imports:  Set[Tuple] = set()    # (module, name, alias)

        conflicts: Dict = {
            "duplicate_tools":              [],
            "duplicate_helpers":            [],
            "duplicate_globals":            [],
            "relative_imports":             [],
            "star_imports":                 [],
            "alias_conflicts":              [],
            "empty_repos":                  [],
            "tools_with_missing_helpers":   [],
        }

        # Track which symbols each repo exports via relative imports so we can
        # warn when a tool references one of them after stripping.
        relative_symbols_by_file: Dict[str, Set[str]] = {}

        for file_path in self._get_py_files():
            repo_name = file_path.relative_to(self.tools_dir).parts[0]
            code = self._read_code(file_path)

            try:
                tree = ast.parse(code)
            except SyntaxError as exc:
                logger.warning(
                    f"[PARSER] Skipping unparseable file {file_path}: {exc}"
                )
                continue

            rel_symbols_this_file: Set[str] = set()

            for node in tree.body:

                # ------------------------------------------------
                # Skip __main__ guards entirely
                # ------------------------------------------------
                if self._is_main_guard(node):
                    continue

                # ------------------------------------------------
                # IMPORTS
                # ------------------------------------------------

                if isinstance(node, ast.Import):
                    for alias in node.names:
                        mod   = alias.name
                        alias_name = alias.asname

                        if mod in seen_plain_imports:
                            existing_alias = seen_plain_imports[mod]
                            if existing_alias != alias_name:
                                # Alias conflict — keep first, record second
                                conflicts["alias_conflicts"].append(
                                    (mod, existing_alias, alias_name, str(file_path))
                                )
                            # Either way — skip, already recorded
                            continue

                        seen_plain_imports[mod] = alias_name
                        structured_imports["import"].append({
                            "module": mod,
                            "alias":  alias_name,
                        })

                elif isinstance(node, ast.ImportFrom):
                    # Relative import — drop but record
                    if node.level and node.level > 0:
                        src = ast.get_source_segment(code, node) or ""
                        conflicts["relative_imports"].append(src)
                        # Track what names this relative import would have provided
                        for alias in node.names:
                            rel_symbols_this_file.add(alias.asname or alias.name)
                        continue

                    mod = node.module or ""

                    for alias in node.names:
                        name       = alias.name
                        alias_name = alias.asname

                        # Star import
                        if name == "*":
                            src = ast.get_source_segment(code, node) or ""
                            conflicts["star_imports"].append(src)
                            # Keep it — we can't know what it provides
                            key = (mod, "*", None)
                            if key not in seen_from_imports:
                                seen_from_imports.add(key)
                                structured_imports["from"].append({
                                    "module": mod,
                                    "name":   "*",
                                    "alias":  None,
                                })
                            continue

                        key = (mod, name, alias_name)
                        if key in seen_from_imports:
                            continue
                        seen_from_imports.add(key)
                        structured_imports["from"].append({
                            "module": mod,
                            "name":   name,
                            "alias":  alias_name,
                        })

                # ------------------------------------------------
                # TOOL FUNCTIONS
                # ------------------------------------------------

                elif isinstance(node, ast.FunctionDef):
                    if self._is_tool_function(node):
                        original_name = node.name
                        final_name    = original_name

                        if original_name in seen_tool_names:
                            # Rename: original__reponame
                            final_name = f"{original_name}__{repo_name}"
                            conflicts["duplicate_tools"].append(
                                (original_name, final_name, str(file_path))
                            )
                            logger.warning(
                                f"[PARSER] Duplicate @tool '{original_name}' in "
                                f"{file_path} — renamed to '{final_name}'"
                            )
                        else:
                            seen_tool_names[original_name] = str(file_path)

                        source = self._extract_function_with_decorators(node, code)
                        if original_name != final_name:
                            source = self._rename_function(source, original_name, final_name)

                        tools.append({
                            "name":   final_name,
                            "source": source,
                            "file":   str(file_path),
                        })

                    # Non-tool helper function
                    else:
                        original_name = node.name
                        final_name    = original_name

                        if original_name in seen_helper_names:
                            final_name = f"{original_name}__{repo_name}"
                            conflicts["duplicate_helpers"].append(
                                (original_name, final_name, str(file_path))
                            )
                            logger.warning(
                                f"[PARSER] Duplicate helper '{original_name}' in "
                                f"{file_path} — renamed to '{final_name}'"
                            )
                        else:
                            seen_helper_names[original_name] = str(file_path)

                        block = ast.get_source_segment(code, node)
                        if block:
                            if original_name != final_name:
                                block = self._rename_function(block, original_name, final_name)
                            utilities.append(block)

                # ------------------------------------------------
                # CLASSES
                # ------------------------------------------------

                elif isinstance(node, ast.ClassDef):
                    block = ast.get_source_segment(code, node)
                    if block:
                        utilities.append(block)

                # ------------------------------------------------
                # GLOBAL ASSIGNMENTS
                # ------------------------------------------------

                elif isinstance(node, ast.Assign):
                    src = ast.get_source_segment(code, node) or ""
                    if "FastMCP" in src:
                        continue

                    # Try to detect simple name assignments for collision checking
                    assigned_names = self._extract_assigned_names(node)

                    renamed = False
                    for var_name in assigned_names:
                        if var_name in seen_global_names:
                            new_name = f"{var_name}__{repo_name}"
                            conflicts["duplicate_globals"].append(
                                (var_name, new_name, str(file_path))
                            )
                            logger.warning(
                                f"[PARSER] Duplicate global '{var_name}' in "
                                f"{file_path} — renamed to '{new_name}'"
                            )
                            src = src.replace(var_name, new_name, 1)
                            renamed = True
                        else:
                            seen_global_names[var_name] = str(file_path)

                    utilities.append(src)

            # Record relative symbols for this file
            if rel_symbols_this_file:
                relative_symbols_by_file[str(file_path)] = rel_symbols_this_file

        # ------------------------------------------------
        # Post-pass: warn about tools referencing dropped relative symbols
        # ------------------------------------------------
        all_relative_symbols = set().union(*relative_symbols_by_file.values()) if relative_symbols_by_file else set()
        if all_relative_symbols:
            for tool in tools:
                for sym in all_relative_symbols:
                    if sym in tool["source"]:
                        conflicts["tools_with_missing_helpers"].append(
                            (tool["name"], sym, tool["file"])
                        )

        # ------------------------------------------------
        # Warn about repos with no tools
        # ------------------------------------------------
        repos_with_tools: Set[str] = set()
        for tool in tools:
            repo = Path(tool["file"]).relative_to(self.tools_dir).parts[0]
            repos_with_tools.add(repo)

        search_roots = self._get_search_roots()
        for root_dir in search_roots:
            if root_dir.name not in repos_with_tools:
                conflicts["empty_repos"].append(root_dir.name)
                logger.warning(
                    f"[PARSER] Repo '{root_dir.name}' contributed zero @tool functions."
                )

        return {
            "imports":   structured_imports,
            "utilities": utilities,
            "tools":     tools,
            "conflicts": conflicts,
        }

    # ==================================================
    # FILE DISCOVERY
    # ==================================================

    def _get_search_roots(self) -> List[Path]:
        if self.allowed_dirs is not None:
            return [Path(d) for d in self.allowed_dirs]
        return [d for d in self.tools_dir.iterdir() if d.is_dir()]

    def _get_py_files(self) -> List[Path]:
        """
        Yield .py files from allowed_dirs only, excluding:
          - known non-code directories (tests, docs, examples, …)
          - known non-tool top-level files (setup.py, conftest.py)
        """
        py_files = []
        for root_dir in self._get_search_roots():
            for root, dirs, files in os.walk(root_dir):
                root_path = Path(root)

                # Prune excluded directories in-place so os.walk won't descend
                dirs[:] = [
                    d for d in dirs
                    if d.lower() not in _EXCLUDED_DIRS
                ]

                for fname in files:
                    if not fname.endswith(".py"):
                        continue

                    # Exclude known non-tool files only at the repo root level
                    if root_path == root_dir and fname in _EXCLUDED_FILES:
                        logger.debug(f"[PARSER] Skipping excluded file: {root_path / fname}")
                        continue

                    py_files.append(root_path / fname)

        return py_files

    def _read_code(self, file_path: Path) -> str:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    # ==================================================
    # NODE CLASSIFICATION
    # ==================================================

    def _is_main_guard(self, node: ast.stmt) -> bool:
        """Return True for `if __name__ == '__main__':` blocks."""
        if not isinstance(node, ast.If):
            return False
        test = node.test
        if not isinstance(test, ast.Compare):
            return False
        if not isinstance(test.left, ast.Name) or test.left.id != "__name__":
            return False
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            return False
        if len(test.comparators) != 1:
            return False
        comp = test.comparators[0]
        return isinstance(comp, ast.Constant) and comp.value == "__main__"

    def _is_tool_function(self, node: ast.FunctionDef) -> bool:
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == "tool":
                return True
            if isinstance(decorator, ast.Attribute) and decorator.attr == "tool":
                return True
            if isinstance(decorator, ast.Call):
                if isinstance(decorator.func, ast.Name) and decorator.func.id == "tool":
                    return True
                if isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "tool":
                    return True
        return False

    # ==================================================
    # SOURCE EXTRACTION & REWRITING
    # ==================================================

    def _extract_function_with_decorators(self, node: ast.FunctionDef, code: str) -> str:
        lines      = code.split("\n")
        start_line = node.lineno - 1
        decorators = []
        idx        = start_line - 1

        while idx >= 0 and lines[idx].strip().startswith("@"):
            decorators.append(lines[idx])
            idx -= 1

        decorators.reverse()
        func_block = decorators + lines[start_line:node.end_lineno]
        return "\n".join(func_block)

    def _rename_function(self, source: str, old_name: str, new_name: str) -> str:
        """
        Replace the function definition name only (not arbitrary occurrences of
        the string).  Handles both `def foo(` and `async def foo(`.
        """
        import re
        # Match `def <old_name>(` with word boundary to avoid partial matches
        pattern = rf'\bdef\s+{re.escape(old_name)}\s*\('
        replacement = f"def {new_name}("
        return re.sub(pattern, replacement, source, count=1)

    def _extract_assigned_names(self, node: ast.Assign) -> List[str]:
        """
        Return simple variable names from an assignment's targets.
        Only handles Name targets (not tuple unpacking, attribute sets, etc.)
        """
        names = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
        return names
