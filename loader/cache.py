import re
import shutil
import subprocess
import logging
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlparse

logger = logging.getLogger("ToolStorePy")

# Default cache location -- inside the package
DEFAULT_CACHE_DIR = Path(__file__).parent.parent / ".repo_cache"


def _derive_repo_key(url: str) -> str:
    """
    Derive a stable, collision-resistant folder/cache name from a git URL.

    Same-named repos from different orgs no longer collide because the org
    (and any subgroup) becomes part of the key.

    Examples:
        https://github.com/alice/tools.git  -> "alice__tools"
        https://github.com/bob/tools.git    -> "bob__tools"
        git@github.com:alice/tools.git      -> "alice__tools"
        https://gitlab.com/g/sub/repo.git   -> "g__sub__repo"
    """
    url = url.rstrip("/")

    # SSH-style: git@host:org/repo.git  (urlparse can't handle this cleanly)
    if url.startswith("git@") and ":" in url and "://" not in url:
        path = url.split(":", 1)[1]
    else:
        path = urlparse(url).path.lstrip("/")

    if path.endswith(".git"):
        path = path[:-4]

    parts = [p for p in path.split("/") if p]
    if not parts:
        # Degenerate URL -- fall back to a hash so we still get a usable key.
        import hashlib
        return "repo-" + hashlib.sha256(url.encode()).hexdigest()[:12]

    key = "__".join(parts)
    # Filesystem-safe: keep alnum/underscore/dot/dash, replace anything else.
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    return key


class RepoCache:
    """
    Manages a local cache of bare git repositories inside the package.

    Workflow:
        - populate(url)            : clone from remote into cache as bare repo (once)
        - get_path(url)            : return local bare repo path for a given URL
        - clone_local(url, target) : fast local clone from cache -> target dir
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------
    # PUBLIC API
    # --------------------------------------------------

    def populate(self, remote_url: str, force: bool = False) -> None:
        """
        Clone remote_url as a bare repo into the cache.
        Skips if already cached unless force=True.
        """
        bare_path = self._bare_path(remote_url)

        if bare_path.exists() and not force:
            logger.debug(f"[CACHE] Already cached: {bare_path.name}")
            return

        if bare_path.exists() and force:
            shutil.rmtree(bare_path)

        logger.info(f"[CACHE] Caching {remote_url} -> {bare_path.name}")

        # Clone into a sibling .tmp dir, then rename atomically. A killed
        # clone leaves a .tmp behind that gets cleaned on next call instead
        # of being mistaken for a healthy bare repo.
        tmp_path = bare_path.with_name(bare_path.name + ".tmp")
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)

        try:
            subprocess.run(
                ["git", "clone", "--bare", "--depth", "1",
                 remote_url, str(tmp_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            tmp_path.replace(bare_path)
        except subprocess.CalledProcessError:
            shutil.rmtree(tmp_path, ignore_errors=True)
            raise

        logger.info(f"[CACHE] Cached {bare_path.name}")

    def populate_many(
        self,
        remote_urls: List[str],
        force: bool = False,
    ) -> List[str]:
        """
        Cache multiple repos, skipping already-cached ones.
        Returns the list of URLs that failed.
        """
        failed: List[str] = []
        for url in remote_urls:
            try:
                self.populate(url, force=force)
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or "").strip()
                logger.error(f"[CACHE] Failed to cache {url}: {stderr}")
                failed.append(url)
        return failed

    def populate_from_local(self, remote_url: str, local_repo: Path) -> bool:
        """
        Populate the cache by bare-cloning an existing local working tree
        rather than re-fetching from the remote. Used to backfill the cache
        after a direct remote clone, so the next build is fast.

        Returns True on success, False otherwise (failure is non-fatal).
        """
        bare_path = self._bare_path(remote_url)
        if bare_path.exists():
            return True

        tmp_path = bare_path.with_name(bare_path.name + ".tmp")
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)

        try:
            subprocess.run(
                ["git", "clone", "--bare", str(local_repo), str(tmp_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            tmp_path.replace(bare_path)
            logger.debug(f"[CACHE] Backfilled from local: {bare_path.name}")
            return True
        except subprocess.CalledProcessError as e:
            shutil.rmtree(tmp_path, ignore_errors=True)
            logger.warning(
                f"[CACHE] Backfill from local failed for {remote_url}: "
                f"{(e.stderr or '').strip()}"
            )
            return False

    def is_cached(self, remote_url: str) -> bool:
        return self._bare_path(remote_url).exists()

    def get_path(self, remote_url: str) -> Optional[Path]:
        p = self._bare_path(remote_url)
        return p if p.exists() else None

    def clone_local(self, remote_url: str, target: Path) -> bool:
        """
        Clone from the local bare cache into target directory.
        Falls back to a remote clone if not cached.

        Returns True if cloned from cache, False if it fell back to remote.
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

        logger.warning(
            f"[CACHE] Not cached, falling back to remote: {remote_url}"
        )
        subprocess.run(
            ["git", "clone", "--depth", "1", remote_url, str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        return False

    def list_cached(self) -> List[str]:
        return [
            p.name for p in self.cache_dir.iterdir()
            if p.is_dir() and not p.name.endswith(".tmp")
        ]

    def clear(self) -> None:
        shutil.rmtree(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[CACHE] Cache cleared.")

    # --------------------------------------------------
    # INTERNAL
    # --------------------------------------------------

    def _bare_path(self, remote_url: str) -> Path:
        """Derive stable, collision-resistant folder name from URL."""
        return self.cache_dir / f"{_derive_repo_key(remote_url)}.git"