# -*- mode: python ; coding: utf-8 -*-
# ruff: noqa: F821 -- PyInstaller injects build classes and SPECPATH.
"""Build from the app directory: python scripts/release.py build.

Personal ad-hoc signed bundle, no Developer ID or notarization claimed.
Models are intentionally external and imported/downloaded after installation.
"""
import ast
import json
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

# A release attempt owns this private metadata file and its build number.
root = Path(SPECPATH)
metadata_path = Path(os.environ['IRIS_CAPTION_BUILD_INFO'])
build_info = json.loads(metadata_path.read_text(encoding='utf-8'))
version_tree = ast.parse((root / 'src/iris_caption/version.py').read_text(encoding='utf-8'))
source_version = next(ast.literal_eval(node.value) for node in version_tree.body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == '__version__'
                              for target in node.targets))
assert build_info['version'] == source_version, 'Staged/source version mismatch'
assert type(build_info['build_number']) is int and build_info['build_number'] > 0
assert build_info['architecture'] == 'arm64'

hiddenimports = ['keyring.backends.macOS', 'sounddevice', 'onnxruntime', 'funasr_onnx',
                 'docx', 'pymupdf', 'sentence_transformers']
hiddenimports += collect_submodules('sentence_transformers', filter=lambda name: '.trainer' not in name and '.training_args' not in name)
hiddenimports += collect_submodules('transformers.models.bert')
hiddenimports += collect_submodules('funasr_onnx')
datas = collect_data_files('funasr_onnx') + collect_data_files('sentence_transformers')
datas += copy_metadata('sentence-transformers')
datas += copy_metadata('torchcodec')
datas += [(str(metadata_path), 'assets')]
datas += [('assets/logo/iris-caption-icon.png', 'assets/logo')]
a = Analysis(['launcher.py'], pathex=['src'], binaries=[], datas=datas,
             hiddenimports=hiddenimports,
             excludes=['PyQt5', 'PyQt6', 'PySide2', 'tensorflow', 'matplotlib', 'IPython'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='IrisCaption',
          debug=False, strip=False, upx=False, console=False,
          target_arch='arm64', codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='IrisCaption')
app = BUNDLE(coll, name='声笺.app', bundle_identifier='local.iris.caption',
              icon='assets/logo/iris-caption.icns',
             version=build_info['version'], info_plist={
                 'CFBundleShortVersionString': build_info['version'],
                 'CFBundleVersion': str(build_info['build_number']),
                 'CFBundleDisplayName': '声笺',
                 'NSMicrophoneUsageDescription': '声笺需要麦克风以在本机生成会议实时字幕。',
                 'NSHighResolutionCapable': True,
                 'LSMinimumSystemVersion': '12.0',
             })
