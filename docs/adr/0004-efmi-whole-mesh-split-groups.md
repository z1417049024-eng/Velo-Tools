# ADR 0004: Preserve Authoring Separations as Whole Mesh Split Groups

- Status: Accepted
- Date: 2026-07-21

## Context

Authors sometimes need one complete body or garment to become several independently switchable meshes, such as upper and lower body variants. A normal Blender separation loses the explicit relationship between those authoring meshes and their generated EFMI Component outputs. Separating the Partition Standard Body also removes the complete projection surface needed by later garments.

## Decision

Velo provides a two-stage Whole Mesh separation operation. The first action enters face selection; the second separates the selected faces, assigns every resulting mesh an independent source identity, and places all related meshes in one stable Whole Mesh Split Group collection under their home Component.

Repeated separation of any member adds another member to the same collection. It never creates a nested split collection. Synchronization mirrors the group as one child collection under every generated `Cx` that contains one of its outputs.

When the Partition Standard Body is separated, Velo retains a hidden intact Partition Projection Guide. The visible separated meshes are normal Whole Meshes and are the only meshes emitted for that body. The guide remains non-exportable and supplies the complete projection surface for later Whole Meshes.

## Consequences

- Separated authoring meshes can receive independent INI Toggle and CrossIB bindings.
- Each separated mesh remains strongly associated with its generated outputs through a stable source ID.
- Collection nesting is bounded to one split-group level under a Component.
- Splitting the standard body does not degrade later garment partition projection.
- Synchronization and output manifests include split-group identity and collection membership.
