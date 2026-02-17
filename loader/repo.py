import subprocess
import logging
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

        self.logger = logging.getLogger("ToolStorePy")

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
            self.logger.info(f"[SKIP] Repository already exists: {folder_name}")
            return

        self.logger.info(f"[CLONE] {folder_name}")

        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(target_path)],
                check=True,
                capture_output=True,
                text=True,
            )

            self.logger.info(f"[SUCCESS] Cloned {folder_name}")

        except subprocess.CalledProcessError as e:
            self.logger.error(f"[FAILED] Could not clone {folder_name}")
            if e.stderr:
                self.logger.error(e.stderr.strip())
            # 🔥 Clone failure should still stop execution
            raise RuntimeError(f"Git clone failed for {repo_url}")

        # Install dependencies if enabled
        if self.install and self.python_exec:
            self._install_requirements(target_path)

    def _derive_folder_name(self, repo_url: str) -> str:
        name = repo_url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return name

    def _install_requirements(self, repo_path: Path):
        requirements_file = repo_path / "requirements.txt"

        if not requirements_file.exists():
            self.logger.debug(f"[DEPS] No requirements.txt found for {repo_path.name}")
            return

        self.logger.info(f"[DEPS] Installing requirements for {repo_path.name}")

        try:
            subprocess.run(
                [
                    str(self.python_exec),
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    str(requirements_file),
                    "--quiet",
                    "--disable-pip-version-check",
                    "--no-warn-script-location",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            self.logger.info(f"[DEPS] Installed dependencies for {repo_path.name}")

        except subprocess.CalledProcessError:
            # 🔥 DO NOT RAISE — continue execution
            self.logger.warning(
                f"[DEPS-SKIPPED] Dependency installation failed for {repo_path.name}. Continuing..."
            )
