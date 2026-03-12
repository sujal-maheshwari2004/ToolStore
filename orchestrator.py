from pathlib import Path
from typing import Optional, List, Iterable
import json
import sys
import subprocess
import venv
import logging

from .config import configure_external_logging
from .index.registry import resolve_index
from .index.downloader import IndexDownloader
from .search.semantic import SemanticSearcher
from .loader.repo import RepoLoader
from .loader.cache import RepoCache
from .builder.mcp_builder import MCPBuilder
from .utils.env_merger import process_env_examples
from .utils.security_scanner import (
    scan_all_repos,
    render_report_text,
    prompt_user_for_risky_repos,
)


class ToolStorePy:
    """
    Main orchestration layer for ToolStorePy.
    """

    def __init__(
        self,
        workspace: str = "toolstorepy_workspace",
        encoder_model: str = "all-MiniLM-L6-v2",
        cross_encoder_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        install_requirements: bool = False,
        verbose: bool = False,
    ):
        self.workspace = Path(workspace)
        self.index_dir = self.workspace / "index_db"
        self.tools_dir = self.workspace / "tools"
        self.output_file = self.workspace / "mcp_unified_server.py"

        self.encoder_model = encoder_model
        self.cross_encoder_model = cross_encoder_model
        self.install_requirements = install_requirements
        self.verbose = verbose

        self._setup_logging()
        configure_external_logging(verbose=self.verbose)

        self._prepare_workspace()

    # --------------------------------------------------
    # LOGGING
    # --------------------------------------------------

    def _setup_logging(self):
        level = logging.DEBUG if self.verbose else logging.INFO
        logging.basicConfig(level=level, format="%(levelname)s | %(message)s")
        self.logger = logging.getLogger("ToolStorePy")

    # --------------------------------------------------
    # WORKSPACE SETUP
    # --------------------------------------------------

    def _prepare_workspace(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.logger.debug("Workspace prepared.")

    # --------------------------------------------------
    # VENV MANAGEMENT
    # --------------------------------------------------

    def _ensure_workspace_venv(self) -> Path:
        venv_path = self.workspace / ".venv"
        newly_created = False

        if not venv_path.exists():
            self.logger.info("Creating workspace virtual environment...")
            builder = venv.EnvBuilder(with_pip=True)
            builder.create(venv_path)
            newly_created = True

        if sys.platform == "win32":
            python_exec = venv_path / "Scripts" / "python"
        else:
            python_exec = venv_path / "bin" / "python"

        subprocess.run(
            [str(python_exec), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        if newly_created:
            self.logger.info("Installing MCP runtime in workspace venv...")
            subprocess.run(
                [str(python_exec), "-m", "pip", "install", "mcp", "mcp[cli]", "--quiet"],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

        return python_exec

    # --------------------------------------------------
    # PUBLIC ENTRYPOINT
    # --------------------------------------------------

    def build(
        self,
        queries: str,
        index: Optional[str] = None,
        index_url: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Path:

        self.logger.info("Resolving index...")
        resolved_url = resolve_index(index=index, index_url=index_url)

        self.logger.info("Downloading index...")
        downloader = IndexDownloader(self.index_dir)
        db_path = downloader.download(resolved_url, force_refresh=force_refresh)

        self.logger.info("Loading queries...")
        query_list = self._load_queries(queries)
        if not query_list:
            raise ValueError("No queries provided.")

        self.logger.info("Running semantic search...")
        matches = self._run_search(query_list, db_path)

        valid_matches = [m for m in matches if m.get("tool_git_link")]
        if not valid_matches:
            raise RuntimeError("No matching tools found for given queries.")

        self.logger.info(f"Found {len(valid_matches)} matching tools.")
        for match in valid_matches:
            name = match.get("tool_name")
            if name:
                self.logger.info(f"✔ Tool selected: {name}")

        unique_links = list({m["tool_git_link"] for m in valid_matches})

        python_exec = None
        if self.install_requirements:
            python_exec = self._ensure_workspace_venv()

        self.logger.info("Cloning repositories...")
        self._clone_repositories(unique_links, python_exec)

        # --------------------------------------------------
        # SECURITY SCAN
        # --------------------------------------------------

        self.logger.info("Running security scan on cloned repositories...")
        scan_reports = scan_all_repos(self.tools_dir)

        report_text = render_report_text(scan_reports)

        # Print to terminal
        print()
        print(report_text)

        # Save to workspace
        report_path = self.workspace / "security_report.txt"
        report_path.write_text(report_text, encoding="utf-8")
        self.logger.info(f"Security report saved → {report_path}")

        # Gate on HIGH findings — prompt user per risky repo
        allowed_repos, skipped_repos = prompt_user_for_risky_repos(scan_reports)

        if skipped_repos:
            self.logger.info(
                f"Skipping {len(skipped_repos)} repo(s) due to HIGH findings: "
                + ", ".join(skipped_repos)
            )

        if not allowed_repos:
            raise RuntimeError(
                "All matched repos were skipped after security review. "
                "Nothing to build."
            )

        # --------------------------------------------------
        # ENV EXAMPLE PROCESSING
        # --------------------------------------------------

        self.logger.info("Scanning for .env.example files...")
        env_keys, missing_keys = process_env_examples(
            tools_dir=self.tools_dir,
            workspace=self.workspace,
        )

        # --------------------------------------------------
        # BUILD
        # --------------------------------------------------

        self.logger.info("Building unified MCP server...")
        builder = MCPBuilder(
            self.tools_dir,
            self.output_file,
            env_keys=env_keys,
            skipped_repos=skipped_repos,
            verbose=self.verbose,
        )
        builder.build()

        # --------------------------------------------------
        # POST-BUILD WARNINGS
        # --------------------------------------------------

        self._print_env_warnings(env_keys, missing_keys)

        # --------------------------------------------------
        # PREPARE VENV (always, so run command is accurate)
        # --------------------------------------------------

        self.logger.info("Preparing runtime environment...")
        python_exec = self._ensure_workspace_venv()

        # --------------------------------------------------
        # INTERACTIVE RUN PROMPT
        # --------------------------------------------------

        self._prompt_and_run(python_exec)

        return self.output_file

    # --------------------------------------------------
    # RUN PROMPT
    # --------------------------------------------------

    def _prompt_and_run(self, python_exec: Path):
        self._print_run_commands(python_exec)
        print()
        while True:
            try:
                answer = input("  ▶  Run the MCP server now? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
                print()

            if answer in ("y", "yes"):
                print()
                self.logger.info("Launching MCP server...")
                subprocess.run([str(python_exec), str(self.output_file)], check=True)
                break
            elif answer in ("n", "no", ""):
                print()
                self.logger.info("Server not started. Use the commands above when ready.")
                break
            else:
                print("  Please enter y or n.")

    def _print_run_commands(self, python_exec: Path):
        width = 62
        border = "─" * width
        simple_cmd = f"python {self.output_file.name}"
        full_cmd   = f"{python_exec.resolve()} {self.output_file.resolve()}"

        def row(content: str) -> str:
            return f"  │{content:<{width}}│"

        print()
        print(f"  ┌{border}┐")
        print(row("  ✅  MCP SERVER BUILT SUCCESSFULLY"))
        print(f"  ├{border}┤")
        print(row("  Server file:"))
        print(row(f"    {self.output_file.resolve()}"))
        print(f"  ├{border}┤")
        print(row("  Simple command  (run from inside workspace dir):"))
        print(row(f"    {simple_cmd}"))
        print(f"  ├{border}┤")
        print(row("  Full command  (run from anywhere):"))
        full_line = f"    {full_cmd}"
        if len(full_line) <= width:
            print(row(full_line))
        else:
            py_part, _, script_part = full_cmd.partition(" ")
            print(row(f"    {py_part} \\"))
            print(row(f"      {script_part}"))
        print(f"  └{border}┘")

    # --------------------------------------------------
    # ENV WARNING OUTPUT
    # --------------------------------------------------

    def _print_env_warnings(self, env_keys: list, missing_keys: list):
        if not env_keys:
            return

        width = 62
        border = "!" * width

        print()
        print(border)
        print("!!  ⚠️  SECRET CONFIGURATION REQUIRED" + " " * 23 + "!!")
        print(border)
        print("!!  One or more of your tools requires environment variables. !!")
        print("!!  A merged .env.example has been written to:               !!")
        print("!!                                                            !!")
        env_path_str = str(self.workspace / ".env.example")
        padding = width - 6 - len(env_path_str)
        print(f"!!    {env_path_str}" + " " * max(padding, 0) + "!!")
        print("!!                                                            !!")
        print("!!  Steps:                                                    !!")
        print("!!    1. Copy .env.example → .env in your workspace          !!")
        print("!!    2. Fill in the required values                          !!")
        print("!!    3. Re-run the server                                    !!")
        print("!!                                                            !!")
        print(f"!!  Required keys ({len(env_keys)}):                                        !!")
        for key in env_keys:
            key_line = f"!!      • {key}"
            print(key_line + " " * (width - len(key_line) - 2) + "!!")
        print(border)

        if missing_keys:
            print()
            print(border)
            print("!!  ❌  MISSING KEYS IN YOUR EXISTING .env" + " " * 19 + "!!")
            print(border)
            print("!!  Found workspace/.env but these keys are absent/empty:    !!")
            for key in missing_keys:
                key_line = f"!!      • {key}"
                print(key_line + " " * (width - len(key_line) - 2) + "!!")
            print(border)

        print()

    # --------------------------------------------------
    # INTERNAL HELPERS
    # --------------------------------------------------

    def _load_queries(self, queries_path: str) -> List[str]:
        with open(queries_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        queries = []
        for item in data:
            if "tool_description" not in item:
                raise ValueError("Each query must contain 'tool_description'.")
            queries.append(item["tool_description"])
        self.logger.debug(f"Loaded {len(queries)} queries.")
        return queries

    def _run_search(self, queries: List[str], db_path: Path):
        searcher = SemanticSearcher(
            persist_dir=db_path,
            encoder_model=self.encoder_model,
            cross_encoder_model=self.cross_encoder_model,
        )
        return searcher.batch_search(queries)

    def _clone_repositories(self, repo_urls: Iterable[str], python_exec: Optional[Path]):
        repo_urls = list(repo_urls)
        cache     = RepoCache()

        missing = [u for u in repo_urls if not cache.is_cached(u)]
        if missing:
            self.logger.info(f"Caching {len(missing)} new repo(s) → {cache.cache_dir}")
            cache.populate_many(missing)
        else:
            self.logger.info(f"All {len(repo_urls)} repo(s) served from cache.")

        loader = RepoLoader(
            self.tools_dir,
            install=self.install_requirements,
            python_exec=python_exec,
            cache=cache,
        )
        loader.process(repo_urls)