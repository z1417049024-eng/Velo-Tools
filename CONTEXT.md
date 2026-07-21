# Velo Tools Domain Glossary

## Component Partition Reference

A joined authoring mesh whose faces retain the discrete EFMI Component identity of the original imported meshes. It is weight-transfer evidence and is never a runtime export object.

## Master Mesh

The complete, user-authored body or garment that remains intact while Velo derives Component outputs from it. A Master Mesh may be reused as a Component Partition Reference for later garments.

## Component Output

A generated, exportable copy of the faces assigned to one EFMI Component. Outputs share their boundary data with adjacent outputs and do not replace the Master Mesh.

## Partition Boundary

The set of existing Master Mesh edges whose incident faces belong to different Components. The boundary does not introduce new topology.

## Block Reassignment (分块归并)

An explicit rule selected from two Partition Standard Body outputs that moves one complete partition block from one EFMI Component label to another. Every Whole Mesh follows the rule; unrelated imported objects in the source Component remain untouched.

## Authoring Zone

The imported EFMI Component tree where authors continue working directly. It contains original imported Component meshes and user-registered complete meshes under their chosen home Components.

## Whole Mesh (整体模型)

A body, garment, or other intact mesh that an author explicitly registers for Component partitioning and assigns to one home Component. Its home Component controls only where it is edited in the Authoring Zone; generated partition routes come from the Partition Standard Body. Original imported Component meshes are not Whole Meshes and are never inferred as such.

## Partition Standard Body

The first body registered as a Whole Mesh. Its projected Component boundaries and Block Reassignment rules are the standard applied to every later Whole Mesh. A hidden Partition Standard Body is omitted from export but remains the partition reference for other Whole Meshes.

## Export Zone

A read-only, exportable mirror of the Authoring Zone. It contains direct `C0` through `C15` Component outputs and is replaced only by a successful synchronization.

## Sync Snapshot

An atomically generated version of the Export Zone whose manifest matches the current Authoring Zone and partition rules. Only a current Sync Snapshot may be exported.

## Passthrough Mapping

An explicit rule that copies one source object or collection unchanged into one target Component. It bypasses geometric partitioning but participates in the same Sync Snapshot.
