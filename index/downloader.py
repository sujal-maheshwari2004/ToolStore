# toolshop/index/downloader.py

import shutil
import zipfile
import tarfile
import hashlib
import requests
from pathlib import Path


class IndexDownloader:
    """
    Handles downloading and extracting vector DB archives.
    """

    def __init__(self, index_root: Path):
        """
        index_root: Path to workspace/index_db
        """
        self.index_root = Path(index_root)
        self.archives_dir = self.index_root / "archives"

        self.index_root.mkdir(parents=True, exist_ok=True)
        self.archives_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def download(self, url: str, force_refresh: bool = False) -> Path:
        """
        Downloads and prepares an index from URL.

        Returns:
            Path to extracted DB directory.
        """

        archive_path = self._download_archive(url, force_refresh)
        extract_path = self._extract_archive(archive_path, force_refresh)

        return extract_path

    # ------------------------------------------------------------------
    # Internal Methods
    # ------------------------------------------------------------------

    def _download_archive(self, url: str, force_refresh: bool) -> Path:
        """
        Download archive into archives directory.
        """

        filename = self._derive_filename(url)
        archive_path = self.archives_dir / filename

        if archive_path.exists() and not force_refresh:
            return archive_path

        response = requests.get(url, stream=True)
        response.raise_for_status()

        with open(archive_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return archive_path

    def _extract_archive(self, archive_path: Path, force_refresh: bool) -> Path:
        """
        Extract archive into isolated folder.
        """

        folder_name = archive_path.stem
        extract_path = self.index_root / folder_name

        if extract_path.exists():
            if force_refresh:
                shutil.rmtree(extract_path)
            else:
                return extract_path

        extract_path.mkdir(parents=True, exist_ok=True)

        if archive_path.suffix == ".zip":
            with zipfile.ZipFile(archive_path, "r") as z:
                z.extractall(extract_path)

        elif archive_path.suffixes[-2:] == [".tar", ".gz"]:
            with tarfile.open(archive_path, "r:gz") as t:
                t.extractall(extract_path)

        else:
            raise ValueError(
                f"Unsupported archive format: {archive_path.name}"
            )

        return extract_path

    def _derive_filename(self, url: str) -> str:
        """
        Safely derive filename from URL.
        If URL does not contain a clean filename,
        fallback to hashed name.
        """

        name = url.split("/")[-1]

        if "." not in name:
            # Fallback: use hash
            hash_id = hashlib.sha256(url.encode()).hexdigest()[:12]
            return f"index-{hash_id}.zip"

        return name
