from pathlib import Path
from typing import Optional, List, Iterable, Tuple
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
    """Main orchestration layer for ToolStorePy."""

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

        self.encoder_model       = encoder_model
        self.cross_encoder_model = cross_encoder_model
        self.install_requirements = install_requirements
        self.verbose             = verbose

        self._setup_logging()
        configure_external_logging(verbose=self.verbose)
        self._prepare_workspace()

    # --------------------------------------------------
    # LOGGING / WORKSPACE
    # --------------------------------------------------

    def _setup_logging(self):
        level = logging.DEBUG if self.verbose else logging.INFO
        logging.basicConfig(level=level, format="%(levelname)s | %(message)s")
        self.logger = logging.getLogger("ToolStorePy")

    def _prepare_workspace(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.logger.debug("Workspace prepared.")

    # --------------------------------------------------
    # VENV MANAGEMENT
    # --------------------------------------------------

    def _ensure_workspace_venv(self) -> Path:
        """
        Create the workspace venv if it doesn't already exist. Idempotent and
        cheap to re-call: pip is only upgraded and the MCP runtime only
        installed on first creation.
        """
        venv_path   = self.workspace / ".venv"
        python_exec = self._venv_python_exec(venv_path)

        if python_exec.exists():
            return python_exec

        self.logger.info("Creating workspace virtual environment...")
        try:
            venv.EnvBuilder(with_pip=True).create(venv_path)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to create venv at {venv_path}: {exc}"
            ) from exc

        python_exec = self._venv_python_exec(venv_path)
        if not python_exec.exists():
            raise RuntimeError(
                f"Created venv at {venv_path} but expected python at "
                f"{python_exec} does not exist."
            )

        self._venv_pip_install(python_exec, ["--upgrade", "pip"], label="upgrade pip")
        self.logger.info("Installing MCP runtime in workspace venv...")
        self._venv_pip_install(python_exec, ["mcp", "mcp[cli]"], label="install mcp runtime")

        return python_exec

    @staticmethod
    def _venv_python_exec(venv_path: Path) -> Path:
        if sys.platform == "win32":
            return venv_path / "Scripts" / "python.exe"
        return venv_path / "bin" / "python"

    def _venv_pip_install(
        self,
        python_exec: Path,
        args: List[str],
        label: str,
    ) -> None:
        """Run pip in the workspace venv; surface failures with stderr."""
        try:
            subprocess.run(
                [str(python_exec), "-m", "pip", "install",
                 "--quiet",
                 "--disable-pip-version-check",
                 "--no-warn-script-location",
                 *args],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr_tail = "\n".join((exc.stderr or "").splitlines()[-10:])
            raise RuntimeError(
                f"pip failed in workspace venv during '{label}'.\n"
                f"{stderr_tail or '(no stderr captured)'}"
            ) from exc

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
                self.logger.info(f"Tool selected: {name}")

        unique_links = list({m["tool_git_link"] for m in valid_matches})

        # If --install-requirements, we need a venv before cloning so the
        # loader can install into it. Otherwise we defer venv creation to
        # just before running.
        python_exec: Optional[Path] = None
        if self.install_requirements:
            python_exec = self._ensure_workspace_venv()

        self.logger.info("Cloning repositories...")
        clone_failures = self._clone_repositories(unique_links, python_exec)

        if clone_failures:
            failed_names = [name for (_, name, _) in clone_failures]
            self.logger.warning(
                f"{len(clone_failures)} repo(s) failed to clone and will be "
                f"excluded: {', '.join(failed_names)}"
            )

        if len(unique_links) - len(clone_failures) == 0:
            raise RuntimeError(
                "No repositories were successfully cloned for this build. "
                "Cannot proceed."
            )

        # --------------------------------------------------
        # SECURITY SCAN
        # --------------------------------------------------

        self.logger.info("Running security scan on cloned repositories...")
        scan_reports = scan_all_repos(self.tools_dir)

        report_text = render_report_text(scan_reports)
        print()
        print(report_text)

        report_path = self.workspace / "security_report.txt"
        report_path.write_text(report_text, encoding="utf-8")
        self.logger.info(f"Security report saved -> {report_path}")

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
        self._print_clone_failure_warning(clone_failures)

        # --------------------------------------------------
        # PREPARE VENV (always, so the run command is accurate)
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
                answer = input("  >  Run the MCP server now? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
                print()

            if answer in ("y", "yes"):
                print()
                self.logger.info("Launching MCP server...")
                try:
                    subprocess.run(
                        [str(python_exec), str(self.output_file)],
                        check=True,
                    )
                except subprocess.CalledProcessError as exc:
                    self.logger.error(
                        f"MCP server exited with status {exc.returncode}."
                    )
                except KeyboardInterrupt:
                    self.logger.info("MCP server interrupted by user.")
                break
            elif answer in ("n", "no", ""):
                print()
                self.logger.info(
                    "Server not started. Use the commands above when ready."
                )
                break
            else:
                print("  Please enter y or n.")

    def _print_run_commands(self, python_exec: Path):
        """
        Plain-ASCII summary of how to run the server. No box-drawing chars
        or emoji so the alignment can't drift across terminals that size
        Unicode glyphs differently.
        """
        sep = "-" * 64
        simple_cmd = f"python {self.output_file.name}"
        full_cmd   = f"{python_exec.resolve()} {self.output_file.resolve()}"

        print()
        print(sep)
        print("  MCP SERVER BUILT SUCCESSFULLY")
        print(sep)
        print("  Server file:")
        print(f"    {self.output_file.resolve()}")
        print()
        print("  Simple command (run from inside workspace dir):")
        print(f"    {simple_cmd}")
        print()
        print("  Full command (run from anywhere):")
        print(f"    {full_cmd}")
        print(sep)

    # --------------------------------------------------
    # WARNINGS / NOTICES
    # --------------------------------------------------

    def _print_env_warnings(self, env_keys: list, missing_keys: list):
        if not env_keys:
            return

        sep = "!" * 64
        print()
        print(sep)
        print("!! SECRET CONFIGURATION REQUIRED")
        print(sep)
        print("!! One or more of your tools requires environment variables.")
        print("!! A merged .env.example has been written to:")
        print(f"!!   {self.workspace / '.env.example'}")
        print("!!")
        print("!! Steps:")
        print("!!   1. Copy .env.example -> .env in your workspace")
        print("!!   2. Fill in the required values")
        print("!!   3. Re-run the server")
        print("!!")
        print(f"!! Required keys ({len(env_keys)}):")
        for key in env_keys:
            print(f"!!   - {key}")
        print(sep)

        if missing_keys:
            print()
            print(sep)
            print("!! MISSING KEYS IN YOUR EXISTING .env")
            print(sep)
            print("!! Found workspace/.env but these keys are absent/empty:")
            for key in missing_keys:
                print(f"!!   - {key}")
            print(sep)

        print()

    def _print_clone_failure_warning(
        self,
        clone_failures: List[Tuple[str, str, str]],
    ):
        if not clone_failures:
            return

        sep = "!" * 64
        print()
        print(sep)
        print("!! REPOSITORIES THAT FAILED TO CLONE")
        print(sep)
        print("!! The following repos could not be cloned and were excluded")
        print("!! from the generated server:")
        print("!!")
        for url, name, stderr in clone_failures:
            short_err = (
                (stderr or "").splitlines()[-1]
                if stderr else "(no error captured)"
            )
            print(f"!!   - {name}")
            print(f"!!     url:   {url}")
            print(f"!!     error: {short_err}")
        print(sep)
        print()

    # --------------------------------------------------
    # INTERNAL HELPERS
    # --------------------------------------------------

    def _load_queries(self, queries_path: str) -> List[str]:
        path = Path(queries_path)
        if not path.exists():
            raise FileNotFoundError(f"Queries file not found: {queries_path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Queries file is not valid JSON ({queries_path}): {exc}"
            ) from exc

        if not isinstance(data, list):
            raise ValueError(
                f"Queries file must contain a JSON list, got "
                f"{type(data).__name__}."
            )

        queries: List[str] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(
                    f"Query #{i} must be an object with 'tool_description'."
                )
            desc = item.get("tool_description")
            if not isinstance(desc, str) or not desc.strip():
                raise ValueError(
                    f"Query #{i} is missing a non-empty 'tool_description'."
                )
            queries.append(desc)

        self.logger.debug(f"Loaded {len(queries)} queries.")
        return queries

    def _run_search(self, queries: List[str], db_path: Path):
        searcher = SemanticSearcher(
            persist_dir=db_path,
            encoder_model=self.encoder_model,
            cross_encoder_model=self.cross_encoder_model,
        )
        return searcher.batch_search(queries)

    def _clone_repositories(
        self,
        repo_urls: Iterable[str],
        python_exec: Optional[Path],
    ) -> List[Tuple[str, str, str]]:
        """
        Clone each repo into the workspace via the shared bare-repo cache.

        RepoLoader now populates the cache itself on miss, so the previous
        two-step (cache.populate_many + loader.process) is collapsed into
        one pass. Returns the loader's failures list -- the orchestrator
        uses this to warn the user about repos that didn't make it in.
        """
        repo_urls = list(repo_urls)
        cache = RepoCache()

        cached_count = sum(1 for u in repo_urls if cache.is_cached(u))
        to_fetch     = len(repo_urls) - cached_count
        self.logger.info(
            f"Cloning {len(repo_urls)} repo(s): "
            f"{cached_count} from cache, {to_fetch} to fetch"
        )

        loader = RepoLoader(
            self.tools_dir,
            install=self.install_requirements,
            python_exec=python_exec,
            cache=cache,
        )
        return loader.process(repo_urls)