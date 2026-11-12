"""Settings with secrets stored exclusively in the macOS keychain."""
from __future__ import annotations

import sys
from pathlib import Path

from .persistence import FileLock, atomic_write_json, migrate_json

SETTINGS_SCHEMA_VERSION = 1
PREFERENCES_SCHEMA_VERSION = 1


APP_NAME = "Iris Caption"


def data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / ".local" / "share" / "iris-caption"


def _read_settings_file(filename: str, version: int) -> dict:
    return migrate_json(data_dir() / filename, data_dir() / "backups", version,
                        lambda value: {**value, "schema_version": version}, lambda value: None)


def read_settings() -> dict:
    with FileLock(data_dir() / '.settings.lock'):
        value = _read_settings_file("settings.json", SETTINGS_SCHEMA_VERSION)
    return {**value, **_speaker_settings(value)}


def _speaker_settings(value: dict) -> dict:
    """Additive schema-1 fields: never coerce strings/numbers to opt-in."""
    return {
        'speakers_enabled': value.get('speakers_enabled') is True,
        'speaker_model_path': value.get('speaker_model_path')
        if isinstance(value.get('speaker_model_path'), str) else '',
    }


def resolve_speaker_model_path(configured: str | None = None, *, model_root: Path | None = None) -> str:
    """Resolve a complete local speaker install, without downloading or saving.

    Runtime verifies pinned artifact hashes before inference. Managed generations
    must carry a validated manifest containing both expected ONNX artifacts.
    """
    from .models import ModelManager
    required = {'campplus.onnx', 'segmentation.onnx'}
    try:
        if configured:
            directory = Path(configured).expanduser()
        else:
            active = ModelManager((model_root or data_dir() / 'models') / 'speaker').active_model()
            if not active or not required <= {entry['path'] for entry in active['manifest']['files']}:
                return ''
            directory = Path(active['directory'])
        if directory.is_symlink() or not directory.is_dir():
            return ''
        for name in required:
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
                return ''
        return str(directory)
    except (OSError, ValueError, TypeError, KeyError):
        return ''


def _verified_vad(directory: Path, manifest: dict) -> bool:
    """Check local model integrity without inference imports or networking."""
    import hashlib
    required = {'config.yaml', 'am.mvn', 'model_quant.onnx'}
    files = {item['path']: item for item in manifest['files']}
    if not required <= files.keys():
        return False
    try:
        for item in manifest['files']:
            path = directory / item['path']
            if (path.is_symlink() or not path.is_file()
                    or not path.resolve().is_relative_to(directory.resolve())
                    or path.stat().st_size != item['size']):
                return False
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != item['sha256']:
                return False
    except OSError:
        return False
    return True


def resolve_vad_model_path(configured: str | None = None) -> str:
    """Resolve only verified known installs; never download or save settings.

    Explicit selections are loaded and checked by the audio worker before capture.
    """
    if configured:
        directory = Path(configured).expanduser()
        return str(directory) if directory.is_dir() else ''
    from .models import ModelManager, builtin_fsmn_manifest
    root = data_dir() / 'models' / 'vad'
    try:
        active = ModelManager(root).active_model()
        if active and _verified_vad(Path(active['directory']), active['manifest']):
            return active['directory']
    except (OSError, ValueError, TypeError, KeyError):
        pass
    manifest = builtin_fsmn_manifest()
    # Modelscope uses both layouts across SDK versions. No broad cache traversal.
    for relative in ('hub/models', 'hub'):
        directory = Path.home() / '.cache' / 'modelscope' / relative / manifest['model_id']
        if _verified_vad(directory, manifest):
            return str(directory)
    return ''


VAD_REQUIRED_MESSAGE = (
    '收音需要语音检测模型（VAD）过滤噪声。请在设置中选择完整的本地 FSMN ONNX 模型，'
    '或点击“下载或导入模型…”安装语音检测模型后重试。'
)


def save_settings(settings: dict) -> None:
    allowed = {key: settings[key] for key in
               ('model_path', 'vad_model_path', 'embedding_model_path', 'deepseek_model', 'feishu_app_id') if key in settings}
    with FileLock(data_dir() / '.settings.lock'):
        current = _read_settings_file("settings.json", SETTINGS_SCHEMA_VERSION)
        current.update(allowed)
        current.update(_speaker_settings({**current, **{key: settings[key] for key in
                       ('speakers_enabled', 'speaker_model_path') if key in settings}}))
        atomic_write_json(data_dir() / "settings.json", current)


def _validated_preferences(value: object) -> dict:
    """Only harmless, bounded view choices may reach the preferences file."""
    if not isinstance(value, dict):
        return {}
    result = {}
    size = value.get("font_size")
    if type(size) is int and 14 <= size <= 48:
        result["font_size"] = size
    if value.get("layout") in ("stacked", "columns"):
        result["layout"] = value["layout"]
    if type(value.get("comparison")) is bool:
        result["comparison"] = value["comparison"]
    name = value.get("microphone_name")
    if isinstance(name, str) and 0 < len(name) <= 512:
        result["microphone_name"] = name
    return result


def read_preferences() -> dict:
    with FileLock(data_dir() / ".view_preferences.lock"):
        value = _read_settings_file("view_preferences.json", PREFERENCES_SCHEMA_VERSION)
    return _validated_preferences(value)


def save_preferences(preferences: dict) -> None:
    with FileLock(data_dir() / ".view_preferences.lock"):
        current = _read_settings_file("view_preferences.json", PREFERENCES_SCHEMA_VERSION)
        current.update(_validated_preferences(preferences))
        atomic_write_json(data_dir() / "view_preferences.json", current)


def get_secret(account: str) -> str:
    import keyring
    return keyring.get_password("iris-caption", account) or ""


def set_secret(account: str, value: str) -> None:
    import keyring
    if value:
        keyring.set_password("iris-caption", account, value)
    else:
        try:
            keyring.delete_password("iris-caption", account)
        except keyring.errors.PasswordDeleteError:
            pass
