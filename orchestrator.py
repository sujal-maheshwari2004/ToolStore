from pathlib import Path
from typing import Optional, List, Iterable
import json

from .index.registry import resolve_index
from .index.downloader import IndexDownloader
from .search.semantic import SemanticSearcher
from .loader.repo import RepoLoader
from .builder.mcp_builder import MCPBuilder


class ToolStorePy:
    """
    Main orchestration layer for ToolStorePy.

    Responsible for coordinating:
        - Index resolution & download
        - Semantic search
        - Repository cloning
        - MCP server synthesis
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

        self._prepare_workspace()

    # --------------------------------------------------
    # WORKSPACE SETUP
    # --------------------------------------------------

    def _prepare_workspace(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.tools_dir.mkdir(parents=True, exist_ok=True)

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
        """
        Full pipeline execution:
            1. Resolve index
            2. Download index
            3. Load queries
            4. Run semantic search
            5. Clone matched repos
            6. Build unified MCP server
        """

        # 1️⃣ Resolve index (built-in name or direct URL)
        resolved_url = resolve_index(index=index, index_url=index_url)

        # 2️⃣ Download & prepare index locally
        downloader = IndexDownloader(self.index_dir)
        db_path = downloader.download(
            resolved_url,
            force_refresh=force_refresh
        )

        # 3️⃣ Load queries from JSON
        query_list = self._load_queries(queries)

        if not query_list:
            raise ValueError("No queries provided.")

        # 4️⃣ Semantic search (1 best tool per query)
        matches = self._run_search(query_list, db_path)

        valid_matches = [
            m for m in matches if m.get("tool_git_link")
        ]

        if not valid_matches:
            raise RuntimeError(
                "No matching tools found for given queries."
            )

        # 5️⃣ Deduplicate repo URLs
        unique_links = {
            m["tool_git_link"]
            for m in valid_matches
        }

        self._clone_repositories(unique_links)

        # 6️⃣ Build unified MCP server
        builder = MCPBuilder(
            self.tools_dir,
            self.output_file,
            verbose=self.verbose  # 👈 Pass verbose down
        )
        builder.build()

        return self.output_file

    # --------------------------------------------------
    # INTERNAL HELPERS
    # --------------------------------------------------

    def _load_queries(self, queries_path: str) -> List[str]:
        """
        Load queries.json and extract tool descriptions.
        """

        with open(queries_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        queries = []

        for item in data:
            if "tool_description" not in item:
                raise ValueError(
                    "Each query must contain 'tool_description'."
                )
            queries.append(item["tool_description"])

        return queries

    def _run_search(self, queries: List[str], db_path: Path):
        """
        Execute semantic search + reranking.
        """

        searcher = SemanticSearcher(
            persist_dir=db_path,
            encoder_model=self.encoder_model,
            cross_encoder_model=self.cross_encoder_model,
        )

        return searcher.batch_search(queries)

    def _clone_repositories(self, repo_urls: Iterable[str]):
        """
        Clone matched repositories into workspace.
        """

        loader = RepoLoader(
            self.tools_dir,
            install=self.install_requirements
        )
        loader.process(repo_urls)
