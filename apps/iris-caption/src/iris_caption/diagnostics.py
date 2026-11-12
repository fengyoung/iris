"""Dependency diagnostics without microphone access, keys, or network calls."""
from __future__ import annotations

import importlib
import json
import platform


def check_runtime() -> dict:
    modules = ('PySide6.QtWidgets', 'sounddevice', 'onnxruntime', 'funasr_onnx',
               'scipy.signal', 'keyring.backends.macOS', 'pymupdf', 'docx',
               'sentence_transformers', 'kaldi_native_fbank',
               'iris_caption.speaker_runtime', 'iris_caption.rolling_speakers')
    results = {}
    for module in modules:
        try:
            importlib.import_module(module)
            results[module] = 'ok'
        except Exception as error:
            import traceback
            results[module] = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
    from .version import build_info
    return {'platform': platform.platform(), 'build': build_info(), 'dependencies': results,
            'ok': all(value == 'ok' for value in results.values())}


def main():
    result = check_runtime()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
