import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ToolStorePy")

# Default cache location — inside the package
DEFAULT_CACHE_DIR = Path(__file__).parent.parent / ".repo_cache"


class RepoCache:
    """
    Manages a local cache of bare git repositories inside the package.
    
    Workflow:
        - populate(url)  : clone from remote into cache as bare repo (once)
        - get_path(url)  : return local bare repo path for a given remote URL
        - clone_from_cache(url, target) : fast local clone from cache → target dir
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------
    # PUBLIC API
    # --------------------------------------------------

    def populate(self, remote_url: str, force: bool = False):
        """
        Clone remote_url as a bare repo into cache.
        Skips if already cached unless force=True.
        """
        bare_path = self._bare_path(remote_url)

        if bare_path.exists() and not force:
            logger.debug(f"[CACHE] Already cached: {bare_path.name}")
            return

        if bare_path.exists() and force:
            import shutil
            shutil.rmtree(bare_path)

        logger.info(f"[CACHE] Caching {remote_url} → {bare_path.name}")

        subprocess.run(
            ["git", "clone", "--bare", "--depth", "1", remote_url, str(bare_path)],
            check=True,
            capture_output=True,
            text=True,
        )

        logger.info(f"[CACHE] Cached {bare_path.name}")

    def populate_many(self, remote_urls: list, force: bool = False):
        """Cache multiple repos, skipping already-cached ones."""
        for url in remote_urls:
            try:
                self.populate(url, force=force)
            except subprocess.CalledProcessError as e:
                logger.error(f"[CACHE] Failed to cache {url}: {e.stderr}")

    def is_cached(self, remote_url: str) -> bool:
        return self._bare_path(remote_url).exists()

    def get_path(self, remote_url: str) -> Optional[Path]:
        p = self._bare_path(remote_url)
        return p if p.exists() else None

    def clone_local(self, remote_url: str, target: Path) -> bool:
        """
        Clone from local bare cache into target directory.
        Falls back to remote if not cached.
        Returns True if cloned from cache, False if fell back to remote.
        """
        bare_path = self._bare_path(remote_url)

        if bare_path.exists():
            subprocess.run(
                ["git", "clone", str(bare_path), str(target)],
                check=True,
                capture_output=True,
                text=True,
            )
            return True

        # fallback to remote
        logger.warning(f"[CACHE] Not cached, falling back to remote: {remote_url}")
        subprocess.run(
            ["git", "clone", "--depth", "1", remote_url, str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        return False

    def list_cached(self) -> list:
        return [p.name for p in self.cache_dir.iterdir() if p.is_dir()]

    def clear(self):
        import shutil
        shutil.rmtree(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[CACHE] Cache cleared.")

    # --------------------------------------------------
    # INTERNAL
    # --------------------------------------------------

    def _bare_path(self, remote_url: str) -> Path:
        """Derive stable folder name from URL."""
        name = remote_url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return self.cache_dir / f"{name}.git"