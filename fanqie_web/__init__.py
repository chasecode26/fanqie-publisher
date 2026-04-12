"""Web interaction helpers and browser JS snippets."""

from .js_snippets import (
    BOOKS_JS,
    DETECT_EDITOR_VOLUMES_JS,
    DETECT_VOLUMES_JS,
    LAST_PUBLISH_JS,
    SELECT_EDITOR_VOLUME_JS,
    SELECT_VOLUME_JS,
)
from .manage_ops import (
    click_manage_row_action,
    go_to_manage_page_number,
    go_to_next_manage_page,
    resolve_edit_url_from_manage,
    scan_manage_row,
    wait_manage_table_ready,
)
from .volume_ops import (
    detect_editor_volumes,
    detect_volumes,
    select_editor_volume,
    select_volume,
)

__all__ = [
    "BOOKS_JS",
    "DETECT_EDITOR_VOLUMES_JS",
    "DETECT_VOLUMES_JS",
    "LAST_PUBLISH_JS",
    "SELECT_EDITOR_VOLUME_JS",
    "SELECT_VOLUME_JS",
    "click_manage_row_action",
    "detect_editor_volumes",
    "detect_volumes",
    "go_to_manage_page_number",
    "go_to_next_manage_page",
    "resolve_edit_url_from_manage",
    "scan_manage_row",
    "select_editor_volume",
    "select_volume",
    "wait_manage_table_ready",
]
