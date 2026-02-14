from typing import Optional


# ------------------------------------------------------------------
# Built-in Index Registry
# ------------------------------------------------------------------

BUILTIN_INDEXES = {
    # Example placeholders — replace with real URLs
    "core-tools": "https://example.com/core-tools-v1.zip",
}


# ------------------------------------------------------------------
# Resolver
# ------------------------------------------------------------------

def resolve_index(
    index: Optional[str] = None,
    index_url: Optional[str] = None,
) -> str:
    """
    Resolve either a built-in index name or a direct index URL.

    Rules:
        - Exactly one of `index` or `index_url` must be provided.
        - If `index` is provided, it must exist in BUILTIN_INDEXES.
        - Returns a download URL string.
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
        return BUILTIN_INDEXES[index]

    return index_url
