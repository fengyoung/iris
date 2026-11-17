"""Offline Vision OCR for images and scanned PDF pages; originals stay untouched."""
from __future__ import annotations

import importlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from .exceptions import ValidationError

MAX_BYTES = 40 * 1024 * 1024
MAX_PAGES = 100
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.bmp'}


def _helper_path() -> Path:
    override = os.environ.get('IRIS_INTERVIEW_OCR_HELPER')
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / 'native-ocr' / '.build' / 'iris-ocr-helper'


def _recognize(image: Path) -> dict:
    helper = _helper_path()
    if not helper.is_file():
        raise ValidationError('本地 OCR helper 尚未编译；请运行 native-ocr/build.sh')
    try:
        result = subprocess.run([str(helper), str(image)], capture_output=True, text=True,
                                encoding='utf-8', timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationError('本地 OCR 无法启动或超时，请检查 helper 和图片') from exc
    if result.returncode:
        raise ValidationError('本地 Vision OCR 失败，请检查图片或重新导入')
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or not isinstance(data.get('text'), str):
            raise ValueError('invalid text')
        confidence = data.get('confidence')
        if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError('invalid confidence')
        paragraphs = data.get('paragraphs')
        if not isinstance(paragraphs, list):
            raise ValueError('invalid paragraphs')
        for item in paragraphs:
            if not isinstance(item, dict) or not isinstance(item.get('text'), str):
                raise ValueError('invalid paragraph')
            score = item.get('confidence')
            if (not isinstance(score, (int, float)) or isinstance(score, bool)
                    or not math.isfinite(score) or not 0 <= score <= 1):
                raise ValueError('invalid paragraph confidence')
        return data
    except (ValueError, TypeError) as exc:
        raise ValidationError('OCR helper 返回的数据无效') from exc


def _render_page(page, output: Path) -> None:
    import fitz
    # Around 216dpi, bounded for oversized scans to avoid excessive allocation.
    scale = min(3.0, 5000 / max(page.rect.width, page.rect.height))
    page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).save(output)


def _extract_pages(source: Path, temporary: Path) -> list[tuple[int, dict]]:
    if source.suffix.lower() != '.pdf':
        return [(1, _recognize(source))]
    import fitz
    results = []
    with fitz.open(source) as document:
        if document.needs_pass:
            raise ValidationError('请先解密 PDF 后再导入')
        if len(document) > MAX_PAGES:
            raise ValidationError('OCR 每次最多处理 100 页，请拆分后导入')
        for number, page in enumerate(document, 1):
            text = page.get_text('text', sort=True)
            if text.strip():
                results.append((number, {'text': text, 'confidence': None, 'paragraphs': [], 'ocr': False}))
            else:
                image = temporary / f'page-{number}.png'
                _render_page(page, image)
                results.append((number, {**_recognize(image), 'ocr': True}))
    return results


def extract_with_ocr(path: str | Path) -> dict:
    """Return resume schema plus OCR confidence/provenance, always unverified.

    PDF text pages retain their extracted text; only pages with no text layer
    are rasterized for OCR. Vision units are editable paragraph candidates,
    not guaranteed semantic paragraphs. No original file is changed.
    """
    source = Path(path)
    if not source.is_file() or source.stat().st_size > MAX_BYTES:
        raise ValidationError('简历文件不存在或超过 40 MB')
    if source.suffix.lower() not in IMAGE_SUFFIXES | {'.pdf'}:
        raise ValidationError('本地 OCR 支持图片与 PDF 扫描页')
    try:
        with tempfile.TemporaryDirectory(prefix='iris-interview-ocr-') as temporary:
            pages = _extract_pages(source, Path(temporary))
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError('简历图片或 PDF 无法读取，请检查文件') from exc
    make_blocks = importlib.import_module('.resume', __package__).make_blocks
    blocks, paragraphs, texts, scores = [], [], [], []
    warnings = ['OCR/提取结果未经确认，须对照原件校对；原始文件未修改',
                '多栏顺序、表格、数字及专有名词可能识别有误；置信度不代表文字正确率']
    for page, result in pages:
        text = result['text']
        if text and not text.endswith('\n'):
            text += '\n'
        texts.append(text)
        page_blocks = make_blocks(text, page)
        blocks.extend(page_blocks)
        score = result.get('confidence')
        if score is not None:
            scores.append(score)
            if score < 0.8:
                warnings.append(f'第 {page} 页 OCR 置信度较低，请重点校对')
        if not text.strip():
            warnings.append(f'第 {page} 页未识别到文字，请粘贴文字或更换清晰图片')
        for paragraph in result['paragraphs']:
            paragraphs.append({**paragraph, 'page': page})
    return {'text': ''.join(texts), 'blocks': blocks, 'warnings': warnings,
            'verified': False, 'source_name': source.name,
            'confidence': sum(scores) / len(scores) if scores else None,
            'paragraphs': paragraphs, 'ocr_engine': 'macOS Vision', 'requires_review': True}
