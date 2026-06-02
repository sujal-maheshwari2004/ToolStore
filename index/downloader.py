import shutil
import zipfile
import tarfile
import hashlib
import tempfile
import logging
import requests
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("ToolStorePy")


# (connect_timeout, read_timeout) in seconds.
DEFAULT_TIMEOUT = (10, 60)

# Cap downloads to a sane size to prevent disk-filling attacks.
DEFAULT_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024  # 500 MB


class IndexDownloader:
    """
    Downloads and extracts vector DB archives with:

      - Atomic file/directory writes (no partial downloads/extractions left
        in the cache to be reused as if they were complete).
      - Path-traversal protection on every archive member (zip-slip and tar
        link escapes), with content-based archive-type detection so a file
        renamed to .zip cannot be silently treated as a tarball.
      - Optional SHA-256 integrity verification.
      - Download size cap and request timeouts.
    """

    def __init__(
        self,
        index_root: Path,
        timeout: tuple = DEFAULT_TIMEOUT,
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    ):
        self.index_root         = Path(index_root)
        self.archives_dir       = self.index_root / "archives"
        self.timeout            = timeout
        self.max_download_bytes = max_download_bytes

        self.index_root.mkdir(parents=True, exist_ok=True)
        self.archives_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def download(
        self,
        url: str,
        force_refresh: bool = False,
        sha256: Optional[str] = None,
    ) -> Path:
        """
        Download and prepare an index from a URL.

        Args:
            url:           Archive URL.
            force_refresh: Re-download even if a cached copy exists.
            sha256:        Optional hex-encoded SHA-256 of the expected
                           archive. If provided, the download is verified
                           and rejected on mismatch.

        Returns:
            Path to the extracted DB directory.
        """
        archive_path = self._download_archive(url, force_refresh, sha256)
        extract_path = self._extract_archive(archive_path, force_refresh)
        return extract_path

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _download_archive(
        self,
        url: str,
        force_refresh: bool,
        sha256: Optional[str],
    ) -> Path:
        filename = self._derive_filename(url)
        archive_path = self.archives_dir / filename
        part_path    = archive_path.with_name(archive_path.name + ".part")

        # Drop any partial file left over from an interrupted previous run.
        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                pass

        if archive_path.exists() and not force_refresh:
            if sha256 and not self._verify_sha256(archive_path, sha256):
                logger.warning(
                    f"[INDEX] Cached archive failed checksum -- re-downloading: "
                    f"{archive_path.name}"
                )
                archive_path.unlink()
            else:
                return archive_path

        logger.info(f"[INDEX] Downloading {url}")

        hasher = hashlib.sha256()
        bytes_written = 0

        with requests.get(url, stream=True, timeout=self.timeout) as resp:
            resp.raise_for_status()

            # If the server reports Content-Length, enforce the cap up front.
            content_length = resp.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError:
                    declared = -1
                if declared > self.max_download_bytes:
                    raise ValueError(
                        f"Archive too large: declared {declared} bytes, "
                        f"limit {self.max_download_bytes}"
                    )

            with open(part_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    bytes_written += len(chunk)
                    if bytes_written > self.max_download_bytes:
                        f.close()
                        part_path.unlink(missing_ok=True)
                        raise ValueError(
                            f"Archive exceeded size cap of "
                            f"{self.max_download_bytes} bytes"
                        )
                    hasher.update(chunk)
                    f.write(chunk)

        # Integrity check before committing the file to its final name.
        if sha256:
            actual = hasher.hexdigest()
            if actual.lower() != sha256.lower():
                part_path.unlink(missing_ok=True)
                raise ValueError(
                    f"Checksum mismatch for {url}\n"
                    f"  expected: {sha256}\n"
                    f"  actual:   {actual}"
                )

        # Atomic rename -- only after a complete (and verified) download.
        part_path.replace(archive_path)
        return archive_path

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _extract_archive(self, archive_path: Path, force_refresh: bool) -> Path:
        folder_name = archive_path.stem
        # For .tar.gz / .tar.bz2 / .tar.xz, .stem still leaves ".tar" behind.
        if folder_name.endswith(".tar"):
            folder_name = folder_name[:-4]

        extract_path = self.index_root / folder_name
        marker_path  = extract_path / ".extracted_ok"

        # Trust an existing extract dir only if the completion marker is there.
        if extract_path.exists():
            if force_refresh:
                shutil.rmtree(extract_path)
            elif marker_path.exists():
                return extract_path
            else:
                logger.warning(
                    f"[INDEX] Removing incomplete extraction: {extract_path}"
                )
                shutil.rmtree(extract_path)

        # Extract to a temp dir on the same filesystem, then rename atomically.
        tmp_extract = Path(tempfile.mkdtemp(
            prefix=folder_name + ".",
            suffix=".tmp",
            dir=self.index_root,
        ))

        try:
            kind = self._classify_archive(archive_path)
            if kind == "zip":
                self._extract_zip_safe(archive_path, tmp_extract)
            elif kind == "tar":
                self._extract_tar_safe(archive_path, tmp_extract)
            else:
                raise ValueError(
                    f"Unsupported or unrecognised archive: {archive_path.name}"
                )

            (tmp_extract / ".extracted_ok").touch()
            tmp_extract.replace(extract_path)
        except Exception:
            shutil.rmtree(tmp_extract, ignore_errors=True)
            raise

        return extract_path

    @staticmethod
    def _classify_archive(archive_path: Path) -> str:
        """
        Identify archive kind from on-disk content (magic bytes), not the
        filename, so a misnamed or spoofed file fails loudly instead of
        being misextracted.
        """
        if zipfile.is_zipfile(archive_path):
            return "zip"
        if tarfile.is_tarfile(archive_path):
            return "tar"
        return "unknown"

    @staticmethod
    def _is_within_directory(directory: Path, target: Path) -> bool:
        directory = directory.resolve()
        try:
            target.resolve().relative_to(directory)
            return True
        except ValueError:
            return False

    def _extract_zip_safe(self, archive_path: Path, dest: Path) -> None:
        dest_resolved = dest.resolve()
        with zipfile.ZipFile(archive_path, "r") as z:
            for member in z.infolist():
                name = member.filename
                self._reject_unsafe_member_name(name)
                target = (dest_resolved / name).resolve()
                if not self._is_within_directory(dest_resolved, target):
                    raise ValueError(f"Zip-slip attempt blocked: {name}")
            # All members verified -- safe to extract.
            z.extractall(dest)

    def _extract_tar_safe(self, archive_path: Path, dest: Path) -> None:
        dest_resolved = dest.resolve()
        # 'r:*' auto-detects gzip / bzip2 / xz transparently.
        with tarfile.open(archive_path, mode="r:*") as t:
            for member in t.getmembers():
                # Reject device files, FIFOs, etc.
                if not (member.isfile() or member.isdir()
                        or member.issym() or member.islnk()):
                    raise ValueError(
                        f"Refusing tar member of unsupported type: "
                        f"{member.name} (type={member.type!r})"
                    )

                self._reject_unsafe_member_name(member.name)

                target = (dest_resolved / member.name).resolve()
                if not self._is_within_directory(dest_resolved, target):
                    raise ValueError(f"Tar-slip attempt blocked: {member.name}")

                # Verify link targets stay inside the extract dir too.
                if member.issym() or member.islnk():
                    link_target = (target.parent / member.linkname).resolve()
                    if not self._is_within_directory(dest_resolved, link_target):
                        raise ValueError(
                            f"Tar link escapes extract dir: "
                            f"{member.name} -> {member.linkname}"
                        )

            # On Python >= 3.12, 'filter=\"data\"' applies the stdlib's strict
            # extraction policy as defense-in-depth on top of our checks.
            try:
                t.extractall(dest, filter="data")
            except TypeError:
                t.extractall(dest)

    @staticmethod
    def _reject_unsafe_member_name(name: str) -> None:
        """Block obviously hostile member names before any path resolution."""
        if not name:
            return
        if name.startswith("/") or name.startswith("\\"):
            raise ValueError(f"Refusing absolute-path archive member: {name}")
        if "\\" in name:
            # ZIP/TAR specify forward slashes; backslashes are a red flag.
            raise ValueError(f"Refusing archive member with backslash: {name}")
        if ".." in Path(name).parts:
            raise ValueError(f"Refusing traversal archive member: {name}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_sha256(path: Path, expected_hex: str) -> bool:
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest().lower() == expected_hex.lower()

    @staticmethod
    def _derive_filename(url: str) -> str:
        """
        Derive a stable filename from a URL, ignoring query strings and
        fragments. Falls back to a hashed name if no usable filename is
        present in the path.
        """
        parsed = urlparse(url)
        candidate = Path(parsed.path).name
        if candidate and "." in candidate:
            return candidate
        hash_id = hashlib.sha256(url.encode()).hexdigest()[:12]
        return f"index-{hash_id}.zip"