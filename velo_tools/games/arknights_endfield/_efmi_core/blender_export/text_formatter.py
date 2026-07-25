

import hashlib
import re


_PINYIN_BY_CHAR = {
    '\u4e0a': 'shang',
    '\u4e0b': 'xia',
    '\u4e1d': 'si',
    '\u4f53': 'ti',
    '\u5185': 'nei',
    '\u534a': 'ban',
    '\u5e26': 'dai',
    '\u624b': 'shou',
    '\u65e0': 'wu',
    '\u6709': 'you',
    '\u6cf3': 'yong',
    '\u73af': 'huan',
    '\u82b1': 'hua',
    '\u8863': 'yi',
    '\u889c': 'wa',
    '\u88e4': 'ku',
    '\u8eab': 'shen',
    '\u8fb9': 'bian',
    '\u817f': 'tui',
    '\u978b': 'xie',
}


class TextFormatter:
    
    @staticmethod
    def extract_name_dupe_id(name):
        parts = name.split('.')
        if len(parts) == 1 or not parts[-1].isdigit():
            return 0, name
        return int(parts[-1]), '.'.join(parts[:-1])
    
    def dedupe_name(self, name, name_list):
        if name not in name_list:
            return name
        dupe_id, original_name = self.extract_name_dupe_id(name)
        for i in range(1, 999-dupe_id):
            new_name = f'{original_name}.{i:03d}'
            if new_name not in name_list:
                return new_name
        return self.dedupe_name(f'{name}.001', name_list)
    
    @staticmethod
    def extract_name_parts(name):
        if not isinstance(name, str):
            if hasattr(name, 'name'):
                name = name.name
            else:
                name = str(name)
        name = re.sub(r'[^\w]+', ' ', name, flags=re.UNICODE)
        parts = list(map(str.lower, map(str.strip, name.split(' '))))
        return parts

    def format_name_camel_case(self, name):
        parts = self.extract_name_parts(name)
        return ''.join(map(str.capitalize, parts))

    def format_ini_identifier(self, name, ignored_parts=()):
        if not isinstance(name, str):
            name = name.name if hasattr(name, 'name') else str(name)

        identifier_parts = []
        for part in self.extract_name_parts(name):
            if not part or part in ignored_parts:
                continue
            converted = []
            for char in part:
                if char.isascii() and (char.isalnum() or char == '_'):
                    converted.append(char)
                elif char in _PINYIN_BY_CHAR:
                    converted.append(_PINYIN_BY_CHAR[char])
                elif char.isalnum():
                    converted.append(f'u{ord(char):x}')
            if converted:
                identifier_parts.append(''.join(converted))

        identifier = '_'.join(identifier_parts)
        if any(not char.isascii() for char in name):
            digest = hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]
            identifier = f'{identifier}_{digest}' if identifier else f'object_{digest}'
        return identifier

    def format_ini_swapvar(self, name):
        return f"$swapvar_{self.format_ini_identifier(name, ('var', 'swap'))}"

    def format_ini_drawvar(self, name):
        return f"$draw_{self.format_ini_identifier(name)}"

    def extract_hotkeys_parts(self, hotkeys):
        hotkeys = hotkeys.upper().replace(',', ' ').replace('+', ' ').replace(';', ' ').replace('-', ' ')
        parts = [x for x in map(str.upper, map(str.strip, hotkeys.split(' '))) if x]
        return parts

    def format_hotkeys(self, hotkeys, join_arg=' '):
        return [join_arg.join(self.extract_hotkeys_parts(binding)) for binding in hotkeys.split(';')]
