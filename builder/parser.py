import os
import io
import re
import ast
import tokenize
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
        - Utility blocks (helpers, classes, globals, and other top-level code)
        - @tool-decorated functions (sync and async)
        - Conflict / warning information

    Renames on collision are applied with an identifier-aware rewrite, so
    references to a renamed symbol within the same file are also updated.

    Only directories listed in `allowed_dirs` are parsed.
    If `allowed_dirs` is None, all subdirectories of tools_dir are parsed.
    """

    def __init__(
        self,
        tools_dir: Path,
        allowed_dirs: Optional[List[Path]] = None,
    ):
        self.tools_dir = Path(tools_dir)
        self.allowed_dirs = (
            [Path(d) for d in allowed_dirs]
            if allowed_dirs is not None
            else None
        )

    # ==================================================
    # PUBLIC ENTRYPOINT
    # ==================================================

    def parse_all(self) -> Dict:
        structured_imports: Dict = {"import": [], "from": []}
        utilities: List[str] = []
        tools: List[Dict] = []

        # Cross-file first-occurrence registries (decide which file gets to
        # keep the original name; later files have their copy renamed).
        seen_tool_names:   Dict[str, str] = {}
        seen_helper_names: Dict[str, str] = {}
        seen_global_names: Dict[str, str] = {}
        seen_class_names:  Dict[str, str] = {}
        seen_plain_imports: Dict[str, Optional[str]] = {}
        seen_from_imports: Set[Tuple] = set()

        conflicts: Dict = {
            "duplicate_tools":              [],
            "duplicate_helpers":            [],
            "duplicate_globals":            [],
            "duplicate_classes":            [],
            "relative_imports":             [],
            "star_imports":                 [],
            "alias_conflicts":              [],
            "empty_repos":                  [],
            "tools_with_missing_helpers":   [],
            "tool_methods_skipped":         [],
            "unparseable_files":            [],
            "decoding_replacements":        [],
        }

        # ------------------------------------------------------------------
        # Read + parse every file once, keep records for two-pass processing.
        # ------------------------------------------------------------------
        file_records: List[Dict] = []
        for file_path in self._get_py_files():
            repo_name = file_path.relative_to(self.tools_dir).parts[0]
            code, had_replacement = self._read_code(file_path)
            if had_replacement:
                conflicts["decoding_replacements"].append(str(file_path))

            try:
                tree = ast.parse(code)
            except SyntaxError as exc:
                logger.warning(
                    f"[PARSER] Skipping unparseable file {file_path}: {exc}"
                )
                conflicts["unparseable_files"].append((str(file_path), str(exc)))
                continue

            file_records.append({
                "path":             file_path,
                "repo":             repo_name,
                "code":             code,
                "tree":             tree,
                "rename_map":       {},     # original_name -> new_name (this file's scope)
                "relative_symbols": set(),
            })

        # ------------------------------------------------------------------
        # PASS 1 - detect duplicates and build per-file rename maps.
        # ------------------------------------------------------------------
        for record in file_records:
            file_path = record["path"]
            repo_name = record["repo"]
            tree      = record["tree"]
            rename_map = record["rename_map"]

            for node in tree.body:
                if self._is_main_guard(node):
                    continue

                # Functions (sync or async)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = node.name
                    if self._is_tool_function(node):
                        if name in seen_tool_names:
                            new_name = f"{name}__{repo_name}"
                            rename_map[name] = new_name
                            conflicts["duplicate_tools"].append(
                                (name, new_name, str(file_path))
                            )
                            logger.warning(
                                f"[PARSER] Duplicate @tool '{name}' in "
                                f"{file_path} -- renamed to '{new_name}'"
                            )
                        else:
                            seen_tool_names[name] = str(file_path)
                    else:
                        if name in seen_helper_names:
                            new_name = f"{name}__{repo_name}"
                            rename_map[name] = new_name
                            conflicts["duplicate_helpers"].append(
                                (name, new_name, str(file_path))
                            )
                            logger.warning(
                                f"[PARSER] Duplicate helper '{name}' in "
                                f"{file_path} -- renamed to '{new_name}'"
                            )
                        else:
                            seen_helper_names[name] = str(file_path)

                # Classes
                elif isinstance(node, ast.ClassDef):
                    name = node.name
                    if name in seen_class_names:
                        new_name = f"{name}__{repo_name}"
                        rename_map[name] = new_name
                        conflicts["duplicate_classes"].append(
                            (name, new_name, str(file_path))
                        )
                        logger.warning(
                            f"[PARSER] Duplicate class '{name}' in "
                            f"{file_path} -- renamed to '{new_name}'"
                        )
                    else:
                        seen_class_names[name] = str(file_path)

                    # Warn about @tool methods inside the class body -- they
                    # are not extracted as standalone tools.
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                                and self._is_tool_function(child):
                            conflicts["tool_methods_skipped"].append(
                                (node.name, child.name, str(file_path))
                            )
                            logger.warning(
                                f"[PARSER] @tool method '{child.name}' in class "
                                f"'{node.name}' ({file_path}) was not extracted "
                                f"as a standalone tool."
                            )

                # Globals (Assign + AnnAssign)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    if self._is_fastmcp_assignment(node):
                        continue
                    for var_name in self._extract_assigned_names(node):
                        if var_name in seen_global_names:
                            new_name = f"{var_name}__{repo_name}"
                            rename_map[var_name] = new_name
                            conflicts["duplicate_globals"].append(
                                (var_name, new_name, str(file_path))
                            )
                            logger.warning(
                                f"[PARSER] Duplicate global '{var_name}' in "
                                f"{file_path} -- renamed to '{new_name}'"
                            )
                        else:
                            seen_global_names[var_name] = str(file_path)

        # ------------------------------------------------------------------
        # PASS 2 - extract blocks, apply per-file rename maps to references.
        # ------------------------------------------------------------------
        for record in file_records:
            file_path  = record["path"]
            code       = record["code"]
            tree       = record["tree"]
            rename_map = record["rename_map"]

            for node in tree.body:
                if self._is_main_guard(node):
                    continue

                # ---- IMPORTS ----------------------------------------------
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        mod = alias.name
                        alias_name = alias.asname
                        if mod in seen_plain_imports:
                            existing_alias = seen_plain_imports[mod]
                            if existing_alias != alias_name:
                                conflicts["alias_conflicts"].append(
                                    (mod, existing_alias, alias_name, str(file_path))
                                )
                            continue
                        seen_plain_imports[mod] = alias_name
                        structured_imports["import"].append({
                            "module": mod,
                            "alias":  alias_name,
                        })

                elif isinstance(node, ast.ImportFrom):
                    # Relative import -- drop but record
                    if node.level and node.level > 0:
                        src = ast.get_source_segment(code, node) or ""
                        conflicts["relative_imports"].append(src)
                        for alias in node.names:
                            record["relative_symbols"].add(
                                alias.asname or alias.name
                            )
                        continue

                    mod = node.module or ""
                    for alias in node.names:
                        name = alias.name
                        alias_name = alias.asname
                        if name == "*":
                            src = ast.get_source_segment(code, node) or ""
                            conflicts["star_imports"].append(src)
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

                # ---- FUNCTIONS (sync + async) ----------------------------
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    source    = self._extract_function_with_decorators(node, code)
                    rewritten = self._apply_rename_map(source, rename_map)

                    if self._is_tool_function(node):
                        final_name = rename_map.get(node.name, node.name)
                        tools.append({
                            "name":   final_name,
                            "source": rewritten,
                            "file":   str(file_path),
                        })
                    else:
                        utilities.append(rewritten)

                # ---- CLASSES ---------------------------------------------
                elif isinstance(node, ast.ClassDef):
                    source = self._extract_class_with_decorators(node, code)
                    if source:
                        utilities.append(self._apply_rename_map(source, rename_map))

                # ---- GLOBAL ASSIGNMENTS ----------------------------------
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    if self._is_fastmcp_assignment(node):
                        continue
                    src = ast.get_source_segment(code, node) or ""
                    if src:
                        utilities.append(self._apply_rename_map(src, rename_map))

                # ---- OTHER MODULE-LEVEL CODE (preserve) ------------------
                elif isinstance(node, (
                    ast.Try, ast.With, ast.AsyncWith,
                    ast.For, ast.AsyncFor, ast.While, ast.If,
                    ast.Expr, ast.Raise, ast.Delete,
                    ast.Global, ast.Nonlocal,
                )):
                    src = ast.get_source_segment(code, node)
                    if src:
                        utilities.append(self._apply_rename_map(src, rename_map))

        # ------------------------------------------------------------------
        # Post-pass: per-file, identifier-aware check for tools referencing
        # symbols that came in via dropped relative imports.
        # ------------------------------------------------------------------
        for record in file_records:
            rel_symbols = record["relative_symbols"]
            if not rel_symbols:
                continue
            file_str = str(record["path"])
            for tool in tools:
                if tool["file"] != file_str:
                    continue
                tool_idents = self._extract_identifiers(tool["source"])
                for sym in rel_symbols:
                    if sym in tool_idents:
                        conflicts["tools_with_missing_helpers"].append(
                            (tool["name"], sym, tool["file"])
                        )

        # ------------------------------------------------------------------
        # Warn about repos with no tools.
        # ------------------------------------------------------------------
        repos_with_tools: Set[str] = set()
        for tool in tools:
            repo = Path(tool["file"]).relative_to(self.tools_dir).parts[0]
            repos_with_tools.add(repo)

        for root_dir in self._get_search_roots():
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
        py_files = []
        for root_dir in self._get_search_roots():
            for root, dirs, files in os.walk(root_dir):
                root_path = Path(root)
                dirs[:] = [d for d in dirs if d.lower() not in _EXCLUDED_DIRS]
                for fname in files:
                    if not fname.endswith(".py"):
                        continue
                    if root_path == root_dir and fname in _EXCLUDED_FILES:
                        logger.debug(
                            f"[PARSER] Skipping excluded file: {root_path / fname}"
                        )
                        continue
                    py_files.append(root_path / fname)
        return py_files

    def _read_code(self, file_path: Path) -> Tuple[str, bool]:
        """
        Read source as UTF-8. If invalid bytes are present, decode with
        replacement and return had_replacement=True so the caller can warn.
        """
        with open(file_path, "rb") as f:
            raw = f.read()
        try:
            return raw.decode("utf-8"), False
        except UnicodeDecodeError:
            decoded = raw.decode("utf-8", errors="replace")
            logger.warning(
                f"[PARSER] Non-UTF-8 bytes in {file_path}; replaced with U+FFFD "
                f"(extracted code may be subtly wrong)."
            )
            return decoded, True

    # ==================================================
    # NODE CLASSIFICATION
    # ==================================================

    def _is_main_guard(self, node: ast.stmt) -> bool:
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

    def _is_tool_function(self, node) -> bool:
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

    def _is_fastmcp_assignment(self, node) -> bool:
        """
        AST-level check: is this assignment's RHS a call to FastMCP(...)?
        Replaces the old `"FastMCP" in src` substring check.
        """
        value = getattr(node, "value", None)
        if value is None or not isinstance(value, ast.Call):
            return False
        func = value.func
        if isinstance(func, ast.Name) and func.id == "FastMCP":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "FastMCP":
            return True
        return False

    # ==================================================
    # SOURCE EXTRACTION
    # ==================================================

    def _extract_function_with_decorators(self, node, code: str) -> str:
        """
        Extract a function (sync or async) including all decorators.
        Uses decorator_list[0].lineno when present so multi-line decorators
        are captured correctly.
        """
        lines = code.split("\n")
        if node.decorator_list:
            start_line = node.decorator_list[0].lineno - 1
        else:
            start_line = node.lineno - 1
        return "\n".join(lines[start_line:node.end_lineno])

    def _extract_class_with_decorators(self, node: ast.ClassDef, code: str) -> str:
        """Same approach for classes, which can also be decorated."""
        lines = code.split("\n")
        if node.decorator_list:
            start_line = node.decorator_list[0].lineno - 1
        else:
            start_line = node.lineno - 1
        return "\n".join(lines[start_line:node.end_lineno])

    def _extract_assigned_names(self, node) -> List[str]:
        """
        Return simple variable names from an assignment's targets.
        Handles ast.Assign (incl. tuple unpacking of plain names) and
        ast.AnnAssign.
        """
        names: List[str] = []
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.append(elt.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.append(node.target.id)
        return names

    # ==================================================
    # IDENTIFIER-AWARE REWRITING
    # ==================================================

    def _apply_rename_map(self, source: str, rename_map: Dict[str, str]) -> str:
        """
        Rewrite identifier references using tokenize so only NAME tokens are
        affected -- strings and comments are left alone. Rewrites every
        occurrence in the block (def site AND call sites), which is what
        keeps renamed helpers/globals callable after extraction.
        """
        if not rename_map:
            return source

        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except tokenize.TokenizeError:
            return self._apply_rename_map_regex(source, rename_map)

        # Build a character-offset table so we can splice the original string
        # without relying on tokenize.untokenize's whitespace reconstruction.
        lines = source.splitlines(keepends=True)
        line_starts = [0]
        for ln in lines:
            line_starts.append(line_starts[-1] + len(ln))

        def pos_to_offset(row: int, col: int) -> int:
            # tokenize rows are 1-indexed; cols are 0-indexed
            if row - 1 >= len(line_starts):
                return len(source)
            return line_starts[row - 1] + col

        substitutions: List[Tuple[int, int, str]] = []
        for tok in tokens:
            if tok.type == tokenize.NAME and tok.string in rename_map:
                start = pos_to_offset(tok.start[0], tok.start[1])
                end   = pos_to_offset(tok.end[0],   tok.end[1])
                substitutions.append((start, end, rename_map[tok.string]))

        # Apply in reverse so earlier offsets stay valid.
        result = source
        for start, end, new_str in reversed(substitutions):
            result = result[:start] + new_str + result[end:]
        return result

    def _apply_rename_map_regex(self, source: str, rename_map: Dict[str, str]) -> str:
        """Fallback: word-boundary regex replace if tokenize fails."""
        for old, new in rename_map.items():
            source = re.sub(rf'\b{re.escape(old)}\b', new, source)
        return source

    def _extract_identifiers(self, source: str) -> Set[str]:
        """Identifier-aware set of NAME tokens in source."""
        try:
            tokens = tokenize.generate_tokens(io.StringIO(source).readline)
            return {tok.string for tok in tokens if tok.type == tokenize.NAME}
        except tokenize.TokenizeError:
            return set(re.findall(r'\b[A-Za-z_]\w*\b', source))