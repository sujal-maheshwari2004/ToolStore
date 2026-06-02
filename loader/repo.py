import subprocess
import logging
from pathlib import Path
from typing import Iterable, Optional, List, Tuple

from .cache import _derive_repo_key


class RepoLoader:
    """
    Clones tool repositories into the workspace, using a local RepoCache
    when available. Individual failures do not abort the whole build:
    successful repos still get processed, and the failed ones end up in
    self.failures for the caller to surface.
    """

    def __init__(
        self,
        tools_dir: Path,
        install: bool = False,
        python_exec: Optional[Path] = None,
        cache=None,   # RepoCache instance, optional
    ):
        self.tools_dir   = Path(tools_dir)
        self.install     = install
        self.python_exec = python_exec
        self.cache       = cache
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("ToolStorePy")

        # (repo_url, folder_name, stderr) for each repo that failed to clone.
        self.failures: List[Tuple[str, str, str]] = []
        # (repo_path_name, stderr_tail) for each pip install that failed.
        self.install_failures: List[Tuple[str, str]] = []

    def process(self, repo_urls: Iterable[str]) -> List[Tuple[str, str, str]]:
        """
        Clone each URL. Returns self.failures for convenience; check it to
        see which repos couldn't be cloned without aborting the build.
        """
        for repo_url in repo_urls:
            self._clone_repo(repo_url)
        return self.failures

    def _clone_repo(self, repo_url: str) -> None:
        folder_name = _derive_repo_key(repo_url)
        target_path = self.tools_dir / folder_name

        if target_path.exists():
            self.logger.info(f"[SKIP] Already exists: {folder_name}")
            return

        self.logger.info(f"[CLONE] {folder_name}")

        try:
            if self.cache:
                # If the cache doesn't have this repo yet, populate it now
                # so future builds are fast. Fall back to direct remote
                # clone if populating fails.
                if not self.cache.is_cached(repo_url):
                    try:
                        self.cache.populate(repo_url)
                    except subprocess.CalledProcessError as e:
                        self.logger.warning(
                            f"[CACHE-MISS] Could not populate cache for "
                            f"{folder_name}; falling back to direct remote "
                            f"clone. ({(e.stderr or '').strip()})"
                        )
                        self._direct_remote_clone(repo_url, target_path)
                        # Backfill the cache from the freshly-cloned tree so
                        # the next build still benefits from a local clone.
                        self.cache.populate_from_local(repo_url, target_path)
                        self._maybe_install(target_path)
                        return

                self.cache.clone_local(repo_url, target_path)
                self.logger.info(f"[CACHE HIT] {folder_name}")
            else:
                self._direct_remote_clone(repo_url, target_path)

        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            self.logger.error(f"[FAILED] {folder_name}: {stderr}")
            self.failures.append((repo_url, folder_name, stderr))
            # Clean up any partial directory git may have left behind.
            if target_path.exists():
                import shutil
                shutil.rmtree(target_path, ignore_errors=True)
            return

        self._maybe_install(target_path)

    def _direct_remote_clone(self, repo_url: str, target_path: Path) -> None:
        """Shallow clone directly from the remote (no cache involvement)."""
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(target_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.logger.info(f"[REMOTE] Cloned {target_path.name}")

    def _maybe_install(self, target_path: Path) -> None:
        if self.install and self.python_exec:
            self._install_requirements(target_path)

    def _install_requirements(self, repo_path: Path) -> None:
        requirements_file = repo_path / "requirements.txt"
        if not requirements_file.exists():
            return

        self.logger.info(f"[DEPS] Installing {repo_path.name}")
        try:
            subprocess.run(
                [str(self.python_exec), "-m", "pip", "install",
                 "-r", str(requirements_file),
                 "--quiet",
                 "--disable-pip-version-check",
                 "--no-warn-script-location"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            # Surface the actual pip error (last lines) instead of swallowing
            # it -- the user otherwise gets a mysterious ImportError later.
            stderr = (e.stderr or "").strip()
            stderr_tail = "\n".join(stderr.splitlines()[-10:])
            self.logger.warning(
                f"[DEPS-SKIPPED] {repo_path.name} -- pip install failed:\n"
                f"{stderr_tail or '(no stderr captured)'}"
            )
            self.install_failures.append((repo_path.name, stderr_tail))