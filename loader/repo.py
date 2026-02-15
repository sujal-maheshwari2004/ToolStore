import subprocess
from pathlib import Path
from typing import Iterable, Optional


class RepoLoader:
    """
    Handles cloning tool repositories into workspace.
    Installs dependencies into a shared workspace virtual environment.
    """

    def __init__(
        self,
        tools_dir: Path,
        install: bool = False,
        python_exec: Optional[Path] = None,
    ):
        self.tools_dir = Path(tools_dir)
        self.install = install
        self.python_exec = python_exec
        self.tools_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------
    # PUBLIC API
    # --------------------------------------------------

    def process(self, repo_urls: Iterable[str]):
        for repo_url in repo_urls:
            self._clone_repo(repo_url)

    # --------------------------------------------------
    # INTERNAL
    # --------------------------------------------------

    def _clone_repo(self, repo_url: str):
        folder_name = self._derive_folder_name(repo_url)
        target_path = self.tools_dir / folder_name

        if target_path.exists():
            return

        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(target_path)],
            check=True,
        )

        if self.install and self.python_exec:
            self._install_requirements(target_path)

    def _derive_folder_name(self, repo_url: str) -> str:
        name = repo_url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return name

    def _install_requirements(self, repo_path: Path):
        requirements_file = repo_path / "requirements.txt"

        if requirements_file.exists():
            subprocess.run(
                [
                    str(self.python_exec),
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    str(requirements_file),
                ],
                check=True,
            )
