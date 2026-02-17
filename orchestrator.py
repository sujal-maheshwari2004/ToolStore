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
from .builder.mcp_builder import MCPBuilder


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

        logging.basicConfig(
            level=level,
            format="%(levelname)s | %(message)s",
        )

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
        """
        Create or reuse workspace-level virtual environment.
        Ensures MCP is installed.
        """
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

        # Upgrade pip silently
        subprocess.run(
            [str(python_exec), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Install MCP only once
        if newly_created:
            self.logger.info("Installing MCP runtime in workspace venv...")
            subprocess.run(
                [str(python_exec), "-m", "pip", "install", "mcp", "mcp[cli]", "--quiet"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
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
        db_path = downloader.download(
            resolved_url,
            force_refresh=force_refresh
        )

        self.logger.info("Loading queries...")
        query_list = self._load_queries(queries)

        if not query_list:
            raise ValueError("No queries provided.")

        self.logger.info("Running semantic search...")
        matches = self._run_search(query_list, db_path)

        valid_matches = [
            m for m in matches if m.get("tool_git_link")
        ]

        if not valid_matches:
            raise RuntimeError("No matching tools found for given queries.")

        self.logger.info(f"Found {len(valid_matches)} matching tools.")

        for match in valid_matches:
            name = match.get("tool_name")
            if name:
                self.logger.info(f"✔ Tool selected: {name}")

        unique_links = {m["tool_git_link"] for m in valid_matches}

        python_exec = None
        if self.install_requirements:
            python_exec = self._ensure_workspace_venv()

        self.logger.info("Cloning repositories...")
        self._clone_repositories(unique_links, python_exec)

        self.logger.info("Building unified MCP server...")
        builder = MCPBuilder(
            self.tools_dir,
            self.output_file,
            verbose=self.verbose
        )
        builder.build()

        self.logger.info("Preparing runtime environment...")
        python_exec = self._ensure_workspace_venv()

        self.logger.info("Launching MCP server...")
        subprocess.run(
            [str(python_exec), str(self.output_file)],
            check=True,
        )

        return self.output_file

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

    def _clone_repositories(
        self,
        repo_urls: Iterable[str],
        python_exec: Optional[Path],
    ):
        loader = RepoLoader(
            self.tools_dir,
            install=self.install_requirements,
            python_exec=python_exec,
        )

        loader.process(repo_urls)
