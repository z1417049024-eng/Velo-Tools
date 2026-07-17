import os
import bpy
import json
import subprocess
import numpy
from ..config.main_config import *
from .json_utils import *
from .migoto_utils import Fatal, MigotoUtils
from ..migoto.migoto_binary_file import FMTFile


class DrawIBPair:

    def __init__(self):
        self.DrawIB = ""
        self.AliasName = ""


class ConfigUtils:

    '''
    This is a ini generate helper class to reuse functions.
    '''
    key_list = ["x","c","v","b","n","m","j","k","l","o","p","[","]",
                "x","c","v","b","n","m","j","k","l","o","p","[","]",
                "x","c","v","b","n","m","j","k","l","o","p","[","]"]
    max_reasonable_blend_index = 4096

    @classmethod
    def get_mod_switch_key(cls,key_index:int):
        '''
        Default mod switch/toggle key.
        '''

        # 尝试读取Setting.json里的设置，解析错误就还使用默认的
        try:
            setting_json_dict = JsonUtils.LoadFromFile(GlobalConfig.path_main_json())
            print(setting_json_dict)
            mod_switch_key = str(setting_json_dict["ModSwitchKey"])
            mod_switch_key_list = mod_switch_key.split(",")
            print(mod_switch_key_list)
            switch_key_list:list[str] = []
            for switch_key_str in mod_switch_key_list:
                switch_key_list.append(switch_key_str[1:-1])
            cls.key_list = switch_key_list
        except Exception:
            print("解析自定义SwitchKey失败")

        return cls.key_list[key_index]

    @classmethod
    def get_extract_drawib_list_from_workspace_config_json(cls) -> list[DrawIBPair]:
        '''
        从当前工作空间的Config.json中读取DrawIB列表
        '''
        workspace_path = GlobalConfig.path_workspace_folder()

        game_config_path = os.path.join(workspace_path,"Config.json")
        game_config_json = JsonUtils.LoadFromFile(game_config_path)

        draw_ib_list = []
        for item in game_config_json:
            drawib_pair = DrawIBPair()

            drawib_pair.DrawIB =  item["DrawIB"]
            drawib_pair.AliasName = item["Alias"]

            draw_ib_list.append(drawib_pair)

        return draw_ib_list

    @classmethod
    def _load_workspace_import_type_map(cls) -> dict:
        import_json_path = os.path.join(GlobalConfig.path_workspace_folder(), "Import.json")
        if not os.path.exists(import_json_path):
            return {}
        try:
            import_json = JsonUtils.LoadFromFile(import_json_path)
            if isinstance(import_json, dict):
                return import_json
        except Exception as e:
            print("SSMT: failed to read Import.json: " + str(e))
        return {}

    @classmethod
    def _type_folder_name_from_game_type(cls, game_type_name: str) -> str:
        if game_type_name is None:
            return ""
        if game_type_name.startswith("TYPE_"):
            return game_type_name
        return "TYPE_" + game_type_name

    @classmethod
    def _get_import_prefix_list_for_validation(cls, import_folder_path: str) -> list:
        import_prefix_list = cls.get_prefix_list_from_tmp_json(import_folder_path)
        if len(import_prefix_list) != 0:
            return import_prefix_list

        fmt_prefix_list = []
        for filename in os.listdir(import_folder_path):
            if filename.endswith(".fmt"):
                fmt_prefix_list.append(filename[:-4])
        return fmt_prefix_list

    @classmethod
    def _decode_vertex_element(cls, vb_path: str, vertex_count: int, stride: int, offset: int, fmt: str):
        element_size = MigotoUtils.format_size(fmt)
        component_count = MigotoUtils.format_components(fmt)
        dtype = MigotoUtils.get_nptype_from_format(fmt)

        raw = numpy.fromfile(vb_path, dtype=numpy.uint8, count=vertex_count * stride)
        raw = raw.reshape((vertex_count, stride))
        element_bytes = raw[:, offset:offset + element_size].copy()
        return element_bytes.view(dtype).reshape((vertex_count, component_count))

    @classmethod
    def _validate_import_prefix_files(cls, import_folder_path: str, prefix: str, max_blend_index: int) -> tuple[bool, str]:
        fmt_path = os.path.join(import_folder_path, prefix + ".fmt")
        vb_path = os.path.join(import_folder_path, prefix + ".vb")
        ib_path = os.path.join(import_folder_path, prefix + ".ib")

        for file_path in [fmt_path, vb_path, ib_path]:
            if not os.path.exists(file_path):
                return False, "missing file: " + file_path

        fmt_file = FMTFile(fmt_path)
        if fmt_file.stride <= 0:
            return False, prefix + ": invalid vb stride"

        vb_size = os.path.getsize(vb_path)
        ib_size = os.path.getsize(ib_path)
        if vb_size == 0 or ib_size == 0:
            return False, prefix + ": empty vb or ib"
        if vb_size % fmt_file.stride != 0:
            return False, prefix + ": vb size is not aligned to stride"

        vertex_count = int(vb_size / fmt_file.stride)
        max_element_end = 0
        for element in fmt_file.elements:
            element_end = element.AlignedByteOffset + MigotoUtils.format_size(element.Format)
            max_element_end = max(max_element_end, element_end)
        if max_element_end > fmt_file.stride:
            return False, prefix + ": element layout exceeds stride"

        ib_stride = MigotoUtils.format_size(fmt_file.format)
        if ib_stride <= 0:
            return False, prefix + ": invalid ib format"
        if ib_size % ib_stride != 0:
            return False, prefix + ": ib size is not aligned to format"

        ib_count = int(ib_size / ib_stride)
        ib_data = numpy.fromfile(ib_path, dtype=MigotoUtils.get_nptype_from_format(fmt_file.format), count=ib_count)
        if ib_data.size == 0:
            return False, prefix + ": empty ib data"
        if int(ib_data.min()) < 0:
            return False, prefix + ": ib index is negative"
        max_ib_index = int(ib_data.max())
        if max_ib_index >= vertex_count:
            return False, prefix + ": ib index " + str(max_ib_index) + " exceeds vertex count " + str(vertex_count)

        for element in fmt_file.elements:
            if not element.SemanticName.startswith("BLENDINDICES"):
                continue
            blend_indices = cls._decode_vertex_element(
                vb_path=vb_path,
                vertex_count=vertex_count,
                stride=fmt_file.stride,
                offset=element.AlignedByteOffset,
                fmt=element.Format,
            )
            if int(blend_indices.min()) < 0:
                return False, prefix + ": BLENDINDICES contains negative value"
            max_index = int(blend_indices.max())
            if max_index > max_blend_index:
                return False, prefix + ": BLENDINDICES max = " + str(max_index)

        return True, "ok"

    @classmethod
    def _validate_import_folder(cls, import_folder_path: str, max_blend_index: int = None) -> tuple[bool, str]:
        if max_blend_index is None:
            max_blend_index = cls.max_reasonable_blend_index

        tmp_json_path = os.path.join(import_folder_path, "tmp.json")
        if not os.path.exists(tmp_json_path):
            return False, "missing tmp.json"

        try:
            import_prefix_list = cls._get_import_prefix_list_for_validation(import_folder_path)
            if len(import_prefix_list) == 0:
                return False, "no import prefixes"

            for prefix in import_prefix_list:
                is_valid, reason = cls._validate_import_prefix_files(import_folder_path, prefix, max_blend_index)
                if not is_valid:
                    return False, reason
        except Exception as e:
            return False, str(e)

        return True, "ok"

    @classmethod
    def _choose_import_folder(cls, draw_ib: str, import_drawib_folder_path: str, type_folder_path_list: list, preferred_game_type: str) -> str:
        rejected_reason_dict = {}

        preferred_folder_name = cls._type_folder_name_from_game_type(preferred_game_type)
        if preferred_folder_name != "":
            preferred_folder_path = os.path.join(import_drawib_folder_path, preferred_folder_name)
            if os.path.exists(preferred_folder_path):
                is_valid, reason = cls._validate_import_folder(preferred_folder_path)
                if is_valid:
                    return preferred_folder_path
                rejected_reason_dict[preferred_folder_path] = reason
                print("SSMT: rejected preferred import type for " + draw_ib + ": " + preferred_folder_name + " (" + reason + ")")

        gpu_import_folder_path_list = []
        cpu_import_folder_path_list = []
        for import_folder_path in type_folder_path_list:
            dirname = os.path.basename(import_folder_path)
            if dirname.startswith("TYPE_GPU"):
                gpu_import_folder_path_list.append(import_folder_path)
            elif dirname.startswith("TYPE_CPU"):
                cpu_import_folder_path_list.append(import_folder_path)

        for import_folder_path in gpu_import_folder_path_list + cpu_import_folder_path_list:
            if import_folder_path in rejected_reason_dict:
                continue
            is_valid, reason = cls._validate_import_folder(import_folder_path)
            if is_valid:
                return import_folder_path
            rejected_reason_dict[import_folder_path] = reason
            print("SSMT: rejected import type for " + draw_ib + ": " + os.path.basename(import_folder_path) + " (" + reason + ")")

        reason_list = []
        for import_folder_path, reason in rejected_reason_dict.items():
            reason_list.append(os.path.basename(import_folder_path) + ": " + reason)
        raise Fatal("No valid TYPE_* import folder found for DrawIB " + draw_ib + ". " + "; ".join(reason_list))


    @classmethod
    def get_import_drawib_aliasname_folder_path_dict_with_first_match_type(cls)->list:
        output_folder_path = GlobalConfig.path_workspace_folder()

        draw_ib_list= ConfigUtils.get_extract_drawib_list_from_workspace_config_json()
        preferred_import_type_dict = ConfigUtils._load_workspace_import_type_map()

        final_import_folder_path_dict = {}

        for draw_ib_pair in draw_ib_list:
            draw_ib = draw_ib_pair.DrawIB
            alias_name = draw_ib_pair.AliasName

            type_folder_path_list = []

            # print("DrawIB:", draw_ib)
            import_drawib_folder_path = os.path.join(output_folder_path, draw_ib)

            if not os.path.exists(import_drawib_folder_path):
                continue

            dirs = os.listdir(import_drawib_folder_path)
            for dirname in dirs:
                if not dirname.startswith("TYPE_"):
                    continue
                final_import_folder_path = os.path.join(import_drawib_folder_path,dirname)
                if os.path.isdir(final_import_folder_path):
                    type_folder_path_list.append(final_import_folder_path)

            if len(type_folder_path_list) == 0:
                continue

            preferred_game_type = preferred_import_type_dict.get(draw_ib, "")
            import_folder_path = ConfigUtils._choose_import_folder(
                draw_ib=draw_ib,
                import_drawib_folder_path=import_drawib_folder_path,
                type_folder_path_list=type_folder_path_list,
                preferred_game_type=preferred_game_type,
            )
            final_import_folder_path_dict[draw_ib + "_" + alias_name] = import_folder_path

        return final_import_folder_path_dict


    @classmethod
    def get_prefix_list_from_tmp_json(cls,import_folder_path:str) ->list:
        '''
        从tmp.json中读取要从工作空间中一键导入的模型的名称前缀
        '''
        tmp_json_path = os.path.join(import_folder_path, "tmp.json")

        drawib = os.path.basename(import_folder_path)

        if os.path.exists(tmp_json_path):
            tmp_json_file = open(tmp_json_path)
            tmp_json = json.load(tmp_json_file)
            tmp_json_file.close()
            import_prefix_list = tmp_json["ImportModelList"]
            if len(import_prefix_list) == 0:
                import_partname_prefix_list = []
                partname_list = tmp_json["PartNameList"]
                for partname in partname_list:
                    import_partname_prefix_list.append(drawib + "-" + partname)
                return import_partname_prefix_list
            else:
                # import_prefix_list.sort() it's naturally sorted in DBMT so we don't need sort here.
                return import_prefix_list
        else:
            return []


    @classmethod
    def read_tmp_json(cls,import_folder_path:str) ->dict:
        tmp_json_path = os.path.join(import_folder_path, "tmp.json")
        if os.path.exists(tmp_json_path):
            tmp_json_file = open(tmp_json_path)
            tmp_json = json.load(tmp_json_file)
            tmp_json_file.close()
            return tmp_json
        else:
            raise Fatal("Target tmp.json didn't exists: " + tmp_json_path)


    # Read model prefix attribute in fmt file to locate .ib and .vb file.
    # Save lots of space when reverse mod which have same stride but different kinds of D3D11GameType.
    @classmethod
    def get_model_prefix_from_fmt_file(cls,fmt_file_path:str)->str:
        with open(fmt_file_path, 'r') as file:
            for i in range(10):
                line = file.readline().strip()
                if not line:
                    continue
                if line.startswith('prefix:'):
                    return line.split(':')[1].strip()
        return ""
