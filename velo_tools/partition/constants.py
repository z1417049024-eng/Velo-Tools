"""Shared identifiers for EFMI Component partition authoring."""

ROLE_KEY = "velo_partition_role"
PARTITION_ID_KEY = "velo_partition_id"
GENERATED_KEY = "velo_partition_generated"
SOURCE_NAMES_KEY = "velo_partition_source_names"
SOURCE_COMPONENTS_KEY = "velo_partition_source_components"

ROLE_SOURCE = "source"
ROLE_REFERENCE = "reference"
ROLE_MASTER = "master"
ROLE_OUTPUT = "output"
ROLE_DIAGNOSTIC = "diagnostic"

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
