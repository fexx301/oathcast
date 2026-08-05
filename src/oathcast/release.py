"""Non-secret build identity exposed for deployment verification."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


@dataclass(frozen=True)
class ReleaseInfo:
    """Build metadata that is safe to expose from health and forecast headers."""

    release_id: str
    source_sha256: str
    image_digest: str
    contract_version: str = "forecast_contract_v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "source_sha256": self.source_sha256,
            "image_digest": self.image_digest,
            "contract_version": self.contract_version,
        }


def current_release() -> ReleaseInfo:
    """Read deployment identity from non-secret environment variables."""

    return ReleaseInfo(
        release_id=os.getenv("OATHCAST_RELEASE_ID", "development"),
        source_sha256=os.getenv("OATHCAST_SOURCE_SHA256", "unrecorded"),
        image_digest=os.getenv("OATHCAST_IMAGE_DIGEST", "unrecorded"),
    )
