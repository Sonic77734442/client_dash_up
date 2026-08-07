from __future__ import annotations

import os
import re

from fastapi import HTTPException


DEFAULT_META_GRAPH_API_VERSION = "v25.0"


def meta_graph_api_version() -> str:
    raw = str(
        os.getenv("META_GRAPH_API_VERSION", DEFAULT_META_GRAPH_API_VERSION)
        or DEFAULT_META_GRAPH_API_VERSION
    ).strip().strip("'\"").rstrip(".")
    match = re.fullmatch(r"v?(\d+)(?:\.0)?", raw.lower())
    if not match:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "meta_graph_api_version_invalid",
                "message": "META_GRAPH_API_VERSION must look like v25.0",
            },
        )
    return f"v{int(match.group(1))}.0"
