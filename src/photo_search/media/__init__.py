"""P1-03 derived media: orientation-normalised thumbnails and previews.

The package reads catalogued assets, decodes the user's originals read-only, publishes
content-addressed artifacts into a sharded, rebuildable cache under the data root and
isolates unreadable/unsupported files so one bad input never fails a batch. It performs
no AI inference and never writes to the catalog (see ``pipeline`` for the decision
record): the artifact file itself is the honest "ready" marker.
"""

from photo_search.media.contract import (
    PREVIEW,
    PREVIEW_SPEC,
    THUMBNAIL,
    THUMBNAIL_SPEC,
    MediaSpec,
    spec_for,
)
from photo_search.media.pipeline import (
    MediaAttempt,
    MediaGenerator,
    MediaInterrupted,
    MediaTarget,
    select_targets,
)
from photo_search.media.status import media_status

__all__ = [
    "PREVIEW",
    "PREVIEW_SPEC",
    "THUMBNAIL",
    "THUMBNAIL_SPEC",
    "MediaAttempt",
    "MediaGenerator",
    "MediaInterrupted",
    "MediaSpec",
    "MediaTarget",
    "media_status",
    "select_targets",
    "spec_for",
]
