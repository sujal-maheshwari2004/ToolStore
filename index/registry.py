from typing import Optional, Tuple


# ------------------------------------------------------------------
# Built-in Index Registry
# ------------------------------------------------------------------
# Each entry is {"url": "...", "sha256": "..."|None}.
# sha256 is None while the release is pending; once the release is
# published, fill in the hex digest of the zip so downloader.py's
# integrity check fires automatically on the default path.
# ------------------------------------------------------------------

BUILTIN_INDEXES = {
    "core-tools": {
        "url": (
            "https://github.com/sujal-maheshwari2004/ToolStore"
            "/releases/download/v0.1.0/core-tools-v1.zip"
        ),
        # Fill in after publishing the release:
        #   sha256sum core-tools-v1.zip
        "sha256": None,
    },
}


# ------------------------------------------------------------------
# Resolver
# ------------------------------------------------------------------

def resolve_index(
    index: Optional[str] = None,
    index_url: Optional[str] = None,
    index_sha256: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    Resolve either a built-in index name or a direct index URL.

    Rules:
        - Exactly one of ``index`` or ``index_url`` must be provided.
        - If ``index`` is provided it must exist in BUILTIN_INDEXES.
        - ``index_sha256`` is only meaningful when ``index_url`` is used;
          for built-in indexes the sha256 stored in BUILTIN_INDEXES is
          authoritative and ``index_sha256`` is silently ignored.

    Returns:
        (url, sha256) where sha256 may be None if not known.
    """
    if index and index_url:
        raise ValueError(
            "Provide either 'index' or 'index_url', not both."
        )

    if not index and not index_url:
        raise ValueError(
            "Either 'index' or 'index_url' must be provided."
        )

    if index:
        if index not in BUILTIN_INDEXES:
            available = ", ".join(BUILTIN_INDEXES.keys())
            raise ValueError(
                f"Unknown index '{index}'. "
                f"Available indexes: {available}"
            )
        entry = BUILTIN_INDEXES[index]
        return entry["url"], entry["sha256"]

    # Direct URL: caller may optionally supply a checksum.
    return index_url, index_sha256