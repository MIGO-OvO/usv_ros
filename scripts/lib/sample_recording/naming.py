"""Portable recording names shared by acquisition, storage and downloads."""
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f]')
_NAMED_FILE = re.compile(r'-([0-9]{8}_[0-9]{6}_[0-9]{6})\.json$')


def recording_name(value, required=False):
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise ValueError('采样名称应为文本')
    name = value.strip()
    if required and not name:
        raise ValueError('请输入采样名称')
    if len(name) > 80 or len(name.encode('utf-8')) > 180:
        raise ValueError('名称过长，请控制在 80 字、180 个 UTF-8 字节以内')
    if _FORBIDDEN.search(name) or name.endswith('.'):
        raise ValueError('名称不能包含路径分隔符、控制字符或 <>:"|?*，也不能以句点结尾')
    return name


def recording_stem(name, timestamp, waypoint_seq=None):
    name = recording_name(name)
    if not name:
        return None
    stamp = datetime.fromtimestamp(float(timestamp), timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S_%f')
    if waypoint_seq is not None:
        if isinstance(waypoint_seq, bool) or int(waypoint_seq) != float(waypoint_seq) or int(waypoint_seq) < 0:
            raise ValueError('航点序号无效')
        name += '-' + str(int(waypoint_seq))
    return name + '-' + stamp


def mission_file_id(filename):
    match = _NAMED_FILE.search(filename)
    if match:
        return match.group(1)
    if filename.startswith('mission_') and filename.endswith('.json'):
        return filename[8:-5]
    return None


def sample_name_from_token(config, token):
    token = float(token)
    if not math.isfinite(token) or not token.is_integer() or not 0 <= token < 2 ** 24:
        raise ValueError('采样名称标识无效')
    if token == 0:
        return ''
    name = config.get('recording', {}).get('sample_names', {}).get(str(int(token)))
    if name is None:
        raise ValueError('采样名称标识不存在，请重新命名后启动')
    return recording_name(name, required=True)


def recording_config(value):
    if not isinstance(value, dict):
        raise ValueError('recording must be an object')
    names = value.get('sample_names', {})
    if not isinstance(names, dict) or len(names) > 64:
        raise ValueError('recording.sample_names must contain at most 64 names')
    normalized = {}
    for token, name in names.items():
        if not isinstance(token, str) or not token.isascii() or not token.isdigit() or not 0 < int(token) < 2 ** 24:
            raise ValueError('采样名称标识无效')
        normalized[str(int(token))] = recording_name(name, required=True)
    return {'mission_name': recording_name(value.get('mission_name')), 'sample_names': normalized}


def attachment_header(filename):
    fallback = re.sub(r'[^A-Za-z0-9_.-]', '_', filename)
    return 'attachment; filename="%s"; filename*=UTF-8\'\'%s' % (fallback, quote(filename, safe=''))
