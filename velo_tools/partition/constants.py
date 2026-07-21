"""Shared identifiers for Merged Component partition authoring."""

ROLE_KEY = "velo_partition_role"
PARTITION_ID_KEY = "velo_partition_id"
GENERATED_KEY = "velo_partition_generated"
SOURCE_NAMES_KEY = "velo_partition_source_names"
SOURCE_COMPONENTS_KEY = "velo_partition_source_components"
ROUTES_KEY = "velo_partition_routes"
PART_INDEX_KEY = "velo_partition_part_index"
PART_COLLECTION_KEY = "velo_partition_part_collection"
SOURCE_ID_KEY = "velo_partition_source_id"
WHOLE_SPLIT_GROUP_KEY = "velo_partition_whole_split_group"
WHOLE_SPLIT_COLLECTION_KEY = "velo_partition_whole_split_collection"
WHOLE_SPLIT_NAME_KEY = "velo_partition_whole_split_name"
WHOLE_SPLIT_GUIDE_KEY = "velo_partition_whole_split_guide"
EXPORT_ZONE_KEY = "velo_partition_export_zone"
SYNC_MANIFEST_KEY = "velo_partition_sync_manifest"
OUTPUT_MANIFEST_KEY = "velo_partition_output_manifest"
SYNC_VERSION_KEY = "velo_partition_sync_version"
PREVIEW_HIDE_KEY = "velo_partition_preview_hidden"

ROLE_SOURCE = "source"
ROLE_REFERENCE = "reference"
ROLE_MASTER = "master"
ROLE_OUTPUT = "output"
ROLE_DIAGNOSTIC = "diagnostic"
ROLE_IMPORTED = "imported"

EXPORT_EXCLUDED_ROLES = frozenset(
    {ROLE_SOURCE, ROLE_REFERENCE, ROLE_MASTER, ROLE_DIAGNOSTIC}
)

COMPONENT_ATTRIBUTE = "velo_partition_component"
CONFIDENCE_ATTRIBUTE = "velo_partition_confidence"
AMBIGUOUS_ATTRIBUTE = "velo_partition_ambiguous"

SOURCE_VERTEX_ATTRIBUTE = "__velo_partition_source_vertex"
SOURCE_FACE_ATTRIBUTE = "__velo_partition_source_face"
SOURCE_LOOP_ATTRIBUTE = "__velo_partition_source_loop"

PREVIOUS_HIDE_VIEWPORT_KEY = "velo_partition_prev_hide_viewport"
PREVIOUS_HIDE_RENDER_KEY = "velo_partition_prev_hide_render"
PREVIOUS_HIDE_GET_KEY = "velo_partition_prev_hide_get"

WORK_COLLECTION_NAME = "Velo Partition"
SYNC_FORMAT_VERSION = 2
