import bpy

class Properties_ImportModel(bpy.types.PropertyGroup):
    skeleton_mode: bpy.props.EnumProperty(
        name="骨架",
        description="Per-Component 保留各 DrawIB 本地编号；Merged 使用 VertexGroupMap.json 统一编号",
        items=[
            ("PER_COMPONENT", "Per-Component", "保留每个 DrawIB 的本地顶点组编号"),
            ("MERGED", "Merged", "按 skeleton buffer 把重复骨骼映射到统一顶点组编号"),
        ],
        default="PER_COMPONENT",
    ) # type: ignore

    merged_frame_analysis: bpy.props.StringProperty(
        name="FrameAnalysis",
        description="生成 ZZZ VertexGroupMap.json 使用的 FrameAnalysis；留空时使用 DBMT 中最新一次抓帧",
        default="",
        subtype="DIR_PATH",
    ) # type: ignore

    model_scale: bpy.props.FloatProperty(
        name="模型导入大小比例",
        description="默认为1.0",
        default=1.0,
    ) # type: ignore

    @classmethod
    def model_scale(cls):
        '''
        bpy.context.scene.VTZZ_properties_import_model.model_scale
        '''
        return bpy.context.scene.VTZZ_properties_import_model.model_scale

    import_flip_scale_x :bpy.props.BoolProperty(
        name="设置Scale的X分量为-1避免模型镜像",
        description="勾选后在导入模型时把缩放的X分量乘以-1，实现镜像效果，还原游戏中原本的样子，解决导入后镜像对调的问题",
        default=False
    ) # type: ignore

    @classmethod
    def import_flip_scale_x(cls):
        '''
        bpy.context.scene.VTZZ_properties_import_model.import_flip_scale_x
        '''
        return bpy.context.scene.VTZZ_properties_import_model.import_flip_scale_x

    import_flip_scale_y :bpy.props.BoolProperty(
        name="设置Scale的Y分量为-1来改变模型朝向",
        description="勾选后在导入模型时把缩放的Y分量乘以-1，实现改变朝向效果，主要用于方便后续绑MMD骨",
        default=False
    ) # type: ignore



    @classmethod
    def import_flip_scale_y(cls):
        '''
        bpy.context.scene.VTZZ_properties_import_model.import_flip_scale_y
        '''
        return bpy.context.scene.VTZZ_properties_import_model.import_flip_scale_y

    import_diffuse_texture: bpy.props.BoolProperty(
        name="导入漫射贴图 (ps-t3)",
        description="导入模型时把当前部件对应 draw call 的 ps-t3 连接到 Base Color",
        default=True,
    ) # type: ignore

    @classmethod
    def import_diffuse_texture(cls):
        return bpy.context.scene.VTZZ_properties_import_model.import_diffuse_texture

    import_normal_texture: bpy.props.BoolProperty(
        name="导入法线贴图 (ps-t4)",
        description="导入模型时把当前部件对应 draw call 的 ps-t4 转为 Blender 法线并连接到 Normal",
        default=True,
    ) # type: ignore

    @classmethod
    def import_normal_texture(cls):
        return bpy.context.scene.VTZZ_properties_import_model.import_normal_texture
