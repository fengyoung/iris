# -*- mode: python ; coding: utf-8 -*-
# ruff: noqa: F821 -- PyInstaller provides Analysis/PYZ/EXE/COLLECT/BUNDLE/SPECPATH.
"""Independent personal macOS build; no install, launch, model download or capture."""
from pathlib import Path
import tomllib

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH)
version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
datas = [(str(root / 'src/iris_interview/assets'), 'iris_interview/assets'),
         (str(root / 'src/iris_interview/config'), 'iris_interview/config')]
datas += collect_data_files('funasr_onnx')
for package in ['funasr-onnx', 'onnxruntime', 'sounddevice', 'scipy', 'numpy', 'PyMuPDF', 'python-docx']:
    datas += copy_metadata(package)
hidden = ['keyring.backends.macOS', 'sounddevice', 'onnxruntime', 'numpy', 'scipy.signal',
          'pymupdf', 'fitz', 'docx', 'iris_interview.ocr', 'iris_interview.transcription',
          'iris_caption.audio', 'iris_caption.streaming_vad', 'iris_caption.segmentation',
          'iris_caption.boundary_alignment', 'iris_caption.recognition_scheduler']
hidden += collect_submodules('funasr_onnx')
a = Analysis([str(root / 'scripts/launcher.py')],
             pathex=[str(root / 'src'), str(root.parent / 'iris-caption/src')],
             binaries=[(str(root / 'native/.build/iris-audio-helper'), 'helpers'),
                       (str(root / 'native-ocr/.build/iris-ocr-helper'), 'helpers')],
             datas=datas, hiddenimports=hidden,
             excludes=['PyQt5', 'PyQt6', 'PySide2', 'tensorflow', 'torch',
                       'sentence_transformers', 'transformers', 'modelscope', 'torchaudio',
                       'funasr', 'lightning', 'pytorch_lightning', 'cv2', 'av', 'yt_dlp',
                       'pytest', 'IPython', 'matplotlib', 'pymupdf.layout', 'iris_caption.app',
                       'iris_caption.window', 'iris_caption.controller'],
             noarchive=False)
# PyMuPDF 2.x's hook collects optional layout weights even if excluded.
# Resume extraction uses text/render APIs and native Vision OCR only.
a.datas = [entry for entry in a.datas
           if not entry[0].replace(chr(92), '/').startswith('pymupdf/layout/')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='IrisInterview',
          debug=False, strip=False, upx=False, console=False, target_arch='arm64',
          codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='IrisInterview')
app = BUNDLE(coll, name='知遇.app', bundle_identifier='local.iris.interview',
             icon=str(root / 'src/iris_interview/assets/Iris-interview.icns'), version=version,
             info_plist={'CFBundleShortVersionString': version, 'CFBundleVersion': version,
                         'CFBundleDisplayName': '知遇', 'LSMinimumSystemVersion': '15.0',
                         'NSHighResolutionCapable': True,
                         'NSMicrophoneUsageDescription': '知遇仅在你点击开始后采集本地声音以生成面试转写。',
                         'NSAudioCaptureUsageDescription': '知遇仅在你点击开始后采集指定会议应用声音以生成面试转写。'})
