import subprocess
from pathlib import Path
from typing import Iterable


class RepoLoader:
    """
    Handles cloning tool repositories into workspace.
    """

    def __init__(self, tools_dir: Path, install: bool = False):
        self.tools_dir = Path(tools_dir)
        self.install = install
        self.tools_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------
    # PUBLIC API
    # --------------------------------------------------

    def process(self, repo_urls: Iterable[str]):
        """
        Clone all repositories provided in repo_urls.
        """

        for repo_url in repo_urls:
            self._clone_repo(repo_url)

    # --------------------------------------------------
    # INTERNAL
    # --------------------------------------------------

    def _clone_repo(self, repo_url: str):
        folder_name = self._derive_folder_name(repo_url)
        target_path = self.tools_dir / folder_name

        if target_path.exists():
            # Skip if already cloned
            return

        # Clone repository
        subprocess.run(
            ["git", "clone", repo_url, str(target_path)],
            check=True,
        )

        # Optionally install requirements
        if self.install:
            self._install_requirements(target_path)

    def _derive_folder_name(self, repo_url: str) -> str:
        """
        Extract repo folder name from URL.
        """
        name = repo_url.rstrip("/").split("/")[-1]

        if name.endswith(".git"):
            name = name[:-4]

        return name

    def _install_requirements(self, repo_path: Path):
        requirements_file = repo_path / "requirements.txt"

        if requirements_file.exists():
            subprocess.run(
                ["pip", "install", "-r", str(requirements_file)],
                check=True,
            )
