import numpy
import itertools
import math
import bpy
import os
import json

from .migoto_binary_file import MigotoBinaryFile

from ..utils.texture_utils import TextureUtils
from ..utils.timer_utils import TimerUtils
from ..utils.migoto_utils import Fatal,MigotoUtils
from ..utils.obj_utils import ExtractedObjectHelper

from ..properties.properties_import_model import Properties_ImportModel
from ..properties.properties_wwmi import Properties_WWMI

from ..config.main_config import GlobalConfig

from bpy_extras.io_utils import unpack_list, axis_conversion


class MeshImportUtils:
    max_reasonable_blend_index = 4096

    '''
    这个类依赖于提供的MigotoBinaryFile进行数据导入和处理
    '''
    @classmethod
    def create_mesh_obj_from_mbf(cls, mbf:MigotoBinaryFile, material_name:str=""):
        TimerUtils.Start("Import 3Dmigoto Raw")
        print("导入模型: " + mbf.mesh_name)

        if not mbf.file_size_check():
            return None

        # 创建mesh和obj
        mesh = bpy.data.meshes.new(mbf.mesh_name)
        obj = bpy.data.objects.new(mesh.name, mesh)

        MeshImportUtils.set_import_coordinate(obj=obj)
        MeshImportUtils.set_import_attributes(obj=obj, mbf=mbf)

        MeshImportUtils.initialize_mesh(mesh, mbf)

        blend_indices = {}
        blend_weights = {}
        texcoords = {}
        shapekeys = {}
        use_normals = False
        normals = []

        for element in mbf.fmt_file.elements:
            data = mbf.vb_data[element.ElementName]

            data = MigotoUtils.apply_format_conversion(data, element.Format)

            if element.SemanticName == "POSITION":
                if len(data[0]) == 4:
                    if ([x[3] for x in data] != [1.0] * len(data)) and ([x[3] for x in data] != [0] * len(data)):
                        # Nico: Blender暂时不支持4D索引，加了也没用，直接不行就报错，转人工处理。
                        raise Fatal('Positions are 4D')
                positions = [(x[0], x[1], x[2]) for x in data]
                if not numpy.isfinite(numpy.asarray(positions, dtype=numpy.float32)).all():
                    raise Fatal('POSITION contains non-finite value')
                mesh.vertices.foreach_set('co', unpack_list(positions))
            elif element.SemanticName.startswith("COLOR"):
                mesh.vertex_colors.new(name=element.ElementName)
                color_layer = mesh.vertex_colors[element.ElementName].data
                vertex_indices = numpy.empty(len(mesh.loops), dtype=numpy.int32)
                mesh.loops.foreach_get('vertex_index', vertex_indices)

                data_np = numpy.asarray(data, dtype=numpy.float32)
                if data_np.ndim == 1:
                    data_np = data_np.reshape((-1, 1))
                if data_np.shape[1] < 4:
                    padding = numpy.zeros((data_np.shape[0], 4 - data_np.shape[1]), dtype=numpy.float32)
                    data_np = numpy.concatenate((data_np, padding), axis=1)
                elif data_np.shape[1] > 4:
                    data_np = data_np[:, :4]

                color_layer.foreach_set('color', data_np[vertex_indices].ravel())
            elif element.SemanticName.startswith("BLENDINDICES"):
                if data.ndim == 1:
                    # 如果data是一维数组，转换为包含元组的2D数组，用于处理只有一个R32_UINT的情况
                    data_2d = numpy.array([(x,) for x in data])
                    blend_indices[element.SemanticIndex] = data_2d
                else:
                    blend_indices[element.SemanticIndex] = data
            elif element.SemanticName.startswith("BLENDWEIGHT"):
                blend_weights[element.SemanticIndex] = data
            elif element.SemanticName.startswith("TEXCOORD"):
                texcoords[element.SemanticIndex] = data
            elif element.SemanticName.startswith("SHAPEKEY"):
                shapekeys[element.SemanticIndex] = data
            elif element.SemanticName.startswith("NORMAL"):
                use_normals = True
                '''
                燕云十六声在导入法线时，必须先进行处理。
                这里要注意一个点，如果dump出来的法线数据，全部是正数的话，说明导出时进行了归一化
                比如燕云的法线就是R8G8B8A8_UNORM格式的，而正常的法线应该是R8G8B8A8_SNORM，说明这里进行了归一化到[0,1]之间
                所以从游戏里导入这种归一化[0,1]的法线时，要反过来操作一下，也就是乘以2再减1范围变为[-1,1]
                Blender的法线范围就是[-1,1]
                这种归一化后到[0,1]的法线，可以减少Shader的计算消耗。
                # (此处感谢 球球 的代码开发)
                '''
                if GlobalConfig.gamename == "YYSLS":
                    print("燕云十六声法线处理")
                    normals = [(x[0] * 2 - 1, x[1] * 2 - 1, x[2] * 2 - 1) for x in data]
                else:
                    normals = [(x[0], x[1], x[2]) for x in data]


            elif element.SemanticName == "TANGENT":
                pass
            elif element.SemanticName == "BINORMAL":
                pass
            else:
                raise Fatal("Unknown ElementName: " + element.ElementName)

        # 导入完之后，如果发现blend_weights是空的，则自动补充默认值为1,0,0,0的BLENDWEIGHTS
        if len(blend_weights) == 0 and len(blend_indices) != 0:
            print("检测到BLENDWEIGHTS为空，但是含有BLENDINDICES数据，特殊情况，默认补充1,0,0,0的BLENDWEIGHTS")
            tmpi = 0
            for blendindices_turple in blend_indices.values():
                # print(blendindices_turple)
                new_dict = []
                for indices in blendindices_turple:
                    new_dict.append((1.0,0,0,0))
                blend_weights[tmpi] = new_dict
                tmpi = tmpi + 1

        MeshImportUtils.import_uv_layers(mesh, obj, texcoords)

        #  metadata.json, if contains then we can import merged vgmap.
        component = None
        if Properties_WWMI.import_merged_vgmap() and GlobalConfig.gamename == "WWMI":
            print("尝试读取Metadata.json")
            metadatajsonpath = os.path.join(os.path.dirname(mbf.fmt_path),'Metadata.json')
            if os.path.exists(metadatajsonpath):
                print("鸣潮读取Metadata.json")
                extracted_object = ExtractedObjectHelper.read_metadata(metadatajsonpath)
                if " " in mbf.mesh_name:
                    partname_count = int(mbf.mesh_name.split(" ")[1])
                    print("import partname count: " + str(partname_count))
                    component = extracted_object.components[partname_count]
        print(len(blend_indices))
        print(len(blend_weights))

        MeshImportUtils.import_vertex_groups(mesh, obj, blend_indices, blend_weights, component)
        MeshImportUtils.import_shapekeys(mesh, obj, shapekeys)

        # Validate closes the loops so they don't disappear after edit mode and probably other important things:
        mesh.validate(verbose=False, clean_customdata=False)
        mesh.update()
        # XXX 这个方法还必须得在mesh.validate和mesh.update之后调用 3.6和4.2都可以用这个
        if use_normals:
            # Blender4.2 移除了mesh.create_normal_splits()
            if bpy.app.version <= (4, 0, 0):
                mesh.use_auto_smooth = True
            mesh.normals_split_custom_set_from_vertices(normals)
            mesh.calc_tangents()


        MeshImportUtils.create_bsdf_with_diffuse_linked(
            obj,
            mesh_name=mbf.mesh_name,
            directory=os.path.dirname(mbf.fmt_path),
            material_name=material_name,
        )
        MeshImportUtils.set_import_rotate_angle(obj=obj, mbf=mbf)
        MeshImportUtils.set_import_scale(obj=obj, mbf=mbf)
        MeshImportUtils.set_import_flip(obj=obj, mbf=mbf)

        TimerUtils.End("Import 3Dmigoto Raw")

        return obj

    @classmethod
    def set_import_attributes(cls, obj, mbf:MigotoBinaryFile):
        '''
        设置导入时的初始属性
        '''
        # 设置默认不重计算TANGNET和COLOR
        # TODO 这里每个游戏的属性都不一样，后面拆分为不同游戏的流程。
        obj["3DMigoto:RecalculateTANGENT"] = False
        obj["3DMigoto:RecalculateCOLOR"] = False
        # 设置GameTypeName，方便在Catter的Properties面板中查看
        obj['3DMigoto:GameTypeName'] = mbf.fmt_file.gametypename


    @classmethod
    def set_import_coordinate(cls,obj):
        '''
        虽然每个游戏导入时的坐标不一致，导致模型朝向都不同，但是不在这里修改，而是在后面根据具体的游戏进行扶正
        '''
        obj.matrix_world = axis_conversion(from_forward='-Z', from_up='Y').to_4x4()


    @classmethod
    def set_import_flip(cls,obj,mbf:MigotoBinaryFile):
        # 导入时翻转模型
        # 优先考虑fmt里的值，其次才考虑全局设置
        if mbf.fmt_file.flip_mirror:
            obj.scale.x = obj.scale.x * -1
        elif Properties_ImportModel.import_flip_scale_x():
            obj.scale.x = obj.scale.x * -1

        if mbf.fmt_file.flip_winding:
            obj.scale.y = obj.scale.y * -1
        elif Properties_ImportModel.import_flip_scale_y():
            obj.scale.y = obj.scale.y * -1

    @classmethod
    def set_import_scale(cls,obj,mbf:MigotoBinaryFile):
        # 设置导入时模型大小比例，Unreal模型常用
        scalefactor = Properties_ImportModel.model_scale()
        if scalefactor == 1.0:
            if mbf.fmt_file.scale != "1.0":
                obj.scale.x = float(mbf.fmt_file.scale)
                obj.scale.y = float(mbf.fmt_file.scale)
                obj.scale.z = float(mbf.fmt_file.scale)
        else:
            obj.scale = scalefactor,scalefactor,scalefactor

    @classmethod
    def set_import_rotate_angle(cls,obj,mbf:MigotoBinaryFile):
        # 设置导入时的模型旋转角度，每个游戏都不一样，由生成fmt的程序控制。
        if mbf.fmt_file.rotate_angle:
            obj.rotation_euler[0] = math.radians(mbf.fmt_file.rotate_angle_x)
            obj.rotation_euler[1] = math.radians(mbf.fmt_file.rotate_angle_y)
            obj.rotation_euler[2] = math.radians(mbf.fmt_file.rotate_angle_z)

    @classmethod
    def initialize_mesh(cls,mesh, mbf:MigotoBinaryFile):
        # 翻转索引顺序以改变面朝向
        # print(mbf.ib_data[0],mbf.ib_data[1],mbf.ib_data[2])
        if mbf.fmt_file.flip_face_orientation:  # 假设你有一个标志位控制是否翻转
            flipped_indices = []
            for i in range(0, len(mbf.ib_data), 3):
                triangle = mbf.ib_data[i:i+3]
                flipped_triangle = triangle[::-1]
                flipped_indices.extend(flipped_triangle)
            mbf.ib_data = flipped_indices
        # print(mbf.ib_data[0],mbf.ib_data[1],mbf.ib_data[2])

        # 导入IB文件设置为mesh的三角形索引
        mesh.loops.add(mbf.ib_count)
        mesh.polygons.add(mbf.ib_polygon_count)
        mesh.loops.foreach_set('vertex_index', mbf.ib_data)
        mesh.polygons.foreach_set('loop_start', [x * 3 for x in range(mbf.ib_polygon_count)])
        mesh.polygons.foreach_set('loop_total', [3] * mbf.ib_polygon_count)

        # 根据vb文件的顶点数设置mesh的顶点数
        mesh.vertices.add(mbf.vb_vertex_count)

    @classmethod
    def import_uv_layers(cls,mesh, obj, texcoords):
        # 预先获取所有循环的顶点索引并转换为numpy数组
        loops = mesh.loops
        vertex_indices = numpy.array([l.vertex_index for l in loops], dtype=numpy.int32)

        for texcoord, data in sorted(texcoords.items()):
            # 将原始数据转换为numpy数组（只需转换一次）
            data_np = numpy.array(data, dtype=numpy.float32)
            dim = data_np.shape[1]

            # 确定需要处理的坐标分量组合
            if dim == 4:
                components_list = ('xy', 'zw')
            elif dim == 2:
                components_list = ('xy',)
            else:
                raise Fatal(f'Unhandled TEXCOORD dimension: {dim}')

            cmap = {'x': 0, 'y': 1, 'z': 2, 'w': 3}

            for components in components_list:
                # 创建UV层
                uv_name = f'TEXCOORD{texcoord if texcoord else ""}.{components}'
                mesh.uv_layers.new(name=uv_name)
                blender_uvs = mesh.uv_layers[uv_name]

                # 获取分量对应的索引
                c0 = cmap[components[0]]
                c1 = cmap[components[1]]

                # 批量计算所有顶点的UV坐标（使用向量化操作）
                uvs = numpy.empty((len(data_np), 2), dtype=numpy.float32)
                uvs[:, 0] = data_np[:, c0]           # U分量
                uvs[:, 1] = 1.0 - data_np[:, c1]     # V分量翻转

                # 通过顶点索引获取循环的UV数据并展平为一维数组
                uv_array = uvs[vertex_indices].ravel()

                # 批量设置UV数据（自动处理numpy数组）
                blender_uvs.data.foreach_set('uv', uv_array)

    @classmethod
    def import_vertex_groups(cls,mesh, obj, blend_indices, blend_weights,component):
        '''
        component: 如果是一键导入WWMI的模型则不为None，其它情况默认为None
        '''
        assert (len(blend_indices) == len(blend_weights))
        if blend_indices:
            # We will need to make sure we re-export the same blend indices later -
            # that they haven't been renumbered. Not positive whether it is better
            # to use the vertex group index, vertex group name or attach some extra
            # data. Make sure the indices and names match:
            if component is None:
                max_blend_index = max(itertools.chain(*itertools.chain(*blend_indices.values())))
                if max_blend_index > cls.max_reasonable_blend_index:
                    raise Fatal("BLENDINDICES max = " + str(max_blend_index) + " exceeds safety limit " + str(cls.max_reasonable_blend_index) + ". The selected TYPE_* folder is probably wrong.")
                num_vertex_groups = max_blend_index + 1
            else:
                num_vertex_groups = max(component.vg_map.values()) + 1

            for i in range(num_vertex_groups):
                obj.vertex_groups.new(name=str(i))
            for vertex in mesh.vertices:
                for semantic_index in sorted(blend_indices.keys()):
                    for i, w in zip(blend_indices[semantic_index][vertex.index],
                                    blend_weights[semantic_index][vertex.index]):
                        if w == 0.0:
                            continue
                        if component is None:
                            obj.vertex_groups[i].add((vertex.index,), w, 'REPLACE')
                        else:
                            # 这里由于C++生成的json文件是无序的，所以我们这里读取的时候要用原始的map而不是转换成列表的索引，避免无序问题
                            obj.vertex_groups[component.vg_map[str(i)]].add((vertex.index,), w, 'REPLACE')

    @classmethod
    def import_shapekeys(cls,mesh, obj, shapekeys):
        if not shapekeys:
            return

        # ========== 基础形状键预处理 ==========
        basis = obj.shape_key_add(name='Basis')
        basis.interpolation = 'KEY_LINEAR'
        obj.data.shape_keys.use_relative = True

        # 批量获取基础顶点坐标（约快200倍）
        vert_count = len(obj.data.vertices)
        basis_co = numpy.empty(vert_count * 3, dtype=numpy.float32)
        basis.data.foreach_get('co', basis_co)
        basis_co = basis_co.reshape(-1, 3)  # 转换为(N,3)形状

        # ========== 批量处理所有形状键 ==========
        for sk_id, offsets in shapekeys.items():
            # 添加新形状键
            new_sk = obj.shape_key_add(name=f'Deform {sk_id}')
            new_sk.interpolation = 'KEY_LINEAR'

            # 转换为NumPy数组（假设offsets是列表的列表）
            offset_arr = numpy.array(offsets, dtype=numpy.float32).reshape(-1, 3)

            # 向量化计算新坐标（比循环快100倍）
            new_co = basis_co + offset_arr

            # 批量写入形状键数据（约快300倍）
            new_sk.data.foreach_set('co', new_co.ravel())

            # 强制解除Blender数据块的引用（重要！避免内存泄漏）
            del new_sk

        # 清理临时数组
        del basis_co, offset_arr, new_co

    @classmethod
    def create_bsdf_with_legacy_diffuse_linked(cls, obj, mesh_name:str, directory:str):
        '''
        自动上DiffuseMap贴图
        '''
        # Credit to Rayvy
        # Изменим имя текстуры, чтобы оно точно совпадало с шаблоном (Change the texture name to match the template exactly)
        material_name = f"{mesh_name}_Material"
        # texture_name = f"{mesh_name}-DiffuseMap.jpg"

        if "." in mesh_name:
            mesh_name_split = str(mesh_name).split(".")[0].split("-")
        else:
            mesh_name_split = str(mesh_name).split("-")

        if len(mesh_name_split) < 2:
            return

        texture_prefix = mesh_name_split[0] + "_" + mesh_name_split[1] # IB Hash


        # 查找是否存在满足条件的转换好的tga贴图文件
        texture_path = None

        texture_suffix = "-DiffuseMap.tga"
        # 查找是否存在满足条件的转换好的tga贴图文件
        texture_path = TextureUtils.find_texture(texture_prefix, texture_suffix, directory)
        # 如果不存在，试试查找jpg文件
        if texture_path is None:
            texture_suffix = "_DiffuseMap.jpg"
            # 查找jpg文件，如果这里没找到的话后面也是正常的，但是这里如果找到了就能起到兼容旧版本jpg文件的作用
            texture_path = TextureUtils.find_texture(texture_prefix, texture_suffix, directory)

        # 如果还不存在，试试查找png文件
        if texture_path is None:
            texture_suffix = "_DiffuseMap.png"
            # 查找jpg文件，如果这里没找到的话后面也是正常的，但是这里如果找到了就能起到兼容旧版本jpg文件的作用
            texture_path = TextureUtils.find_texture(texture_prefix, texture_suffix, directory)

        # Nico: 这里如果没有检测到对应贴图则不创建材质，也不新建BSDF
        # 否则会造成合并模型后，UV编辑界面选择不同材质的UV会跳到不同UV贴图界面导致无法正常编辑的问题
        if texture_path is not None:
            # Создание нового материала (Create new materials)

            # 创建一个材质并且自动创建BSDF节点
            material = bpy.data.materials.new(name=material_name)

            # 启用节点系统。
            material.use_nodes = True

            # Nico: Currently only support EN and ZH-CN
            # 4.2 简体中文是 "原理化 BSDF" 英文是 "Principled BSDF"
            bsdf = material.node_tree.nodes.get("原理化 BSDF")
            if not bsdf:
                # 3.6 简体中文是原理化BSDF 没空格
                bsdf = material.node_tree.nodes.get("原理化BSDF")
            if not bsdf:
                bsdf = material.node_tree.nodes.get("Principled BSDF")

            if bsdf:
                # Поиск текстуры (Search for textures)
                if texture_path:
                    tex_image = material.node_tree.nodes.new('ShaderNodeTexImage')

                    tex_image.image = bpy.data.images.load(texture_path)

                    # 因为tga格式贴图有alpha通道，所以必须用CHANNEL_PACKED才能显示正常颜色
                    tex_image.image.alpha_mode = "CHANNEL_PACKED"

                    # 链接Color到基础色
                    material.node_tree.links.new(bsdf.inputs['Base Color'], tex_image.outputs['Color'])

                # Применение материала к мешу (Materials applied to bags)
                if obj.data.materials:
                    obj.data.materials[0] = material
                else:
                    obj.data.materials.append(material)

    @classmethod
    def create_bsdf_with_diffuse_linked(cls, obj, mesh_name:str, directory:str, material_name:str=""):
        import_diffuse = Properties_ImportModel.import_diffuse_texture()
        import_normal = Properties_ImportModel.import_normal_texture()
        if not import_diffuse and not import_normal:
            return

        mesh_name_split = str(mesh_name).split(".")[0].rsplit("-", 1)
        if len(mesh_name_split) != 2 or not mesh_name_split[1].isdigit():
            return

        draw_ib_directory = os.path.dirname(directory)
        render_texture_directory = os.path.join(draw_ib_directory, "RenderTextures")
        if not os.path.isdir(render_texture_directory):
            if import_diffuse:
                cls.create_bsdf_with_legacy_diffuse_linked(obj, mesh_name, directory)
                if obj.data.materials:
                    if material_name:
                        obj.data.materials[0].name = material_name
                    return

        draw_call_ids = []
        component_map_path = os.path.join(draw_ib_directory, "ComponentName_DrawCallIndexList.json")
        if os.path.isfile(component_map_path):
            try:
                with open(component_map_path, "r", encoding="utf-8-sig") as component_map_file:
                    component_map = json.load(component_map_file)
                draw_call_ids = [str(value) for value in component_map.get(
                    "Component " + mesh_name_split[1], []
                )]
            except (OSError, ValueError, TypeError) as error:
                print("SSMT: unable to read texture draw-call map: " + str(error))

        texture_files = sorted(os.listdir(render_texture_directory)) if os.path.isdir(render_texture_directory) else []

        def find_slot_texture(slot):
            slot_marker = "-ps-t" + str(slot) + "="
            for draw_call_id in draw_call_ids:
                draw_marker = draw_call_id + slot_marker
                for file_name in texture_files:
                    if draw_marker in file_name.lower():
                        return os.path.join(render_texture_directory, file_name)
            if not draw_call_ids:
                for file_name in texture_files:
                    if slot_marker in file_name.lower():
                        return os.path.join(render_texture_directory, file_name)
            return None

        diffuse_path = find_slot_texture(3) if import_diffuse else None
        normal_path = find_slot_texture(4) if import_normal else None

        material = bpy.data.materials.new(name=material_name or f"{mesh_name}_Material")
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        bsdf = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            bpy.data.materials.remove(material)
            return

        if diffuse_path is not None:
            diffuse_node = nodes.new("ShaderNodeTexImage")
            diffuse_node.name = "Diffuse Texture (ps-t3)"
            diffuse_node.label = "Diffuse ps-t3"
            diffuse_node.location = (-620, 180)
            diffuse_node.image = bpy.data.images.load(diffuse_path, check_existing=True)
            diffuse_node.image.alpha_mode = "CHANNEL_PACKED"
            try:
                diffuse_node.image.colorspace_settings.name = "sRGB"
            except TypeError:
                pass
            links.new(diffuse_node.outputs["Color"], bsdf.inputs["Base Color"])

        if normal_path is not None:
            normal_texture = nodes.new("ShaderNodeTexImage")
            normal_texture.name = "Normal Texture (ps-t4)"
            normal_texture.label = "Normal ps-t4"
            normal_texture.location = (-820, -220)
            normal_texture.image = bpy.data.images.load(normal_path, check_existing=True)
            normal_texture.image.alpha_mode = "CHANNEL_PACKED"
            normal_texture.image.colorspace_settings.name = "Non-Color"

            invert_color = nodes.new("ShaderNodeInvert")
            invert_color.name = "Invert Normal Color"
            invert_color.label = "Invert"
            invert_color.inputs[0].default_value = 1.0
            invert_color.location = (-560, -220)

            rgb_curves = nodes.new("ShaderNodeRGBCurve")
            rgb_curves.name = "Normal Blue RGB Curves"
            rgb_curves.label = "Blue = 1"
            rgb_curves.inputs[0].default_value = 1.0
            blue_curve = rgb_curves.mapping.curves[2]
            blue_curve.points[0].location = (0.0, 1.0)
            blue_curve.points[-1].location = (1.0, 1.0)
            rgb_curves.mapping.update()

            # CurveMapping.cur is not exposed through RNA. Blender's DNA struct stores
            # it as the second int; set it to 2 so the node UI opens on the B channel.
            import ctypes
            class _CurveMappingHeader(ctypes.Structure):
                _fields_ = [("flag", ctypes.c_int), ("cur", ctypes.c_int)]
            _CurveMappingHeader.from_address(rgb_curves.mapping.as_pointer()).cur = 2
            rgb_curves.location = (-300, -220)

            normal_map = nodes.new("ShaderNodeNormalMap")
            normal_map.name = "Normal Map (Blender)"
            normal_map.space = "TANGENT"
            normal_map.location = (-20, -180)

            links.new(normal_texture.outputs["Color"], invert_color.inputs["Color"])
            links.new(invert_color.outputs["Color"], rgb_curves.inputs["Color"])
            links.new(rgb_curves.outputs["Color"], normal_map.inputs["Color"])
            links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

        if obj.data.materials:
            obj.data.materials[0] = material
        else:
            obj.data.materials.append(material)
