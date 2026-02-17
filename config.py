import os
import logging

def configure_external_logging(verbose: bool = False):
    """
    Suppress noisy external libraries (HuggingFace, urllib3, Chroma, etc.)
    """

    # Disable telemetry
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    os.environ["CHROMA_TELEMETRY"] = "False"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TRANSFORMERS_VERBOSITY"] = "error"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    level = logging.DEBUG if verbose else logging.ERROR

    logging.getLogger("transformers").setLevel(level)
    logging.getLogger("sentence_transformers").setLevel(level)
    logging.getLogger("huggingface_hub").setLevel(level)
    logging.getLogger("httpx").setLevel(level)
    logging.getLogger("urllib3").setLevel(level)
    logging.getLogger("chromadb").setLevel(level)

    try:
        from transformers import logging as hf_logging
        hf_logging.set_verbosity_error()
    except Exception:
        pass
