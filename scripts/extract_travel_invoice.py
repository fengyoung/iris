#!/usr/bin/env python3
"""网约车行程单差旅报销信息提取工具。

双阶段流水线：
  Stage1: 优先直接提取 PDF 文字（文字型 PDF），降级为 adv_model 多模态理解图片（扫描件）
  Stage2: base_model (Deepseek) 合并/排序/区分差旅 → 输出报销汇总

用法：
  python scripts/extract_travel_invoice.py <文件路径1> [文件路径2 ...]
  python scripts/extract_travel_invoice.py --output 输出路径.md <文件路径1> [...]
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

# ── Iris 项目路径 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from iris.config import load_config_bundle
from iris.llm import LLMProviderError, LLMService


# ── Prompt ─────────────────────────────────────────────────────

STAGE1_TEXT_PROMPT = """你是一个专业的票据解析助手。以下是从网约车行程单 PDF 中直接提取的文字内容，请解析所有行程信息。

注意：文字可能因 PDF 排版而跨行断开，请根据上下文合并还原完整字段。
行程单头部会标注年份（如"行程起止日期：2026-05-24 至 2026-06-30"），请用该年份补全所有日期。

要求：
1. 逐条提取每一笔行程记录，包括：日期、时间、起点、终点、金额
2. 日期格式补全为 YYYY-MM-DD，时间格式为 HH:MM
3. 判断起点或终点是否为机场或火车站（仅限城际交通枢纽，如"深圳宝安机场"、"北京西站"等）。
   城市内地铁站不属于城际车站，不要标记为机场/车站。
4. 输出格式为 JSON 数组，每条记录包含以下字段：
   - date: 日期 (YYYY-MM-DD 格式)
   - time: 时间 (HH:MM 格式)
   - origin: 起点
   - destination: 终点
   - amount: 金额（数字，保留两位小数）
   - is_airport_or_station: 布尔值，仅当起点或终点为机场或城际火车站时为 true
5. 只需输出 JSON 数组，不要添加其他文字说明

以下是 PDF 文字内容：
{text}"""

STAGE1_PROMPT = """你是一个专业的票据识别助手。请仔细查看这张行程单截图，提取所有行程信息。

要求：
1. 逐条提取每一笔行程记录，包括：日期、时间、起点、终点、金额
2. **特别注意年份识别**：仔细区分票据上的年份是"2026"还是"2020"。
   "6"和"0"在打印字体中容易混淆，请结合行程单上下文综合判断：
   - 如果同一张单据上大部分日期是2026年，则所有日期都应为2026年
   - 如果起点/终点涉及近期地名或新开通的地点，优先判定为2026年
   - 不要机械照搬印刷数字，要结合年份一致性进行纠错
3. 判断起点或终点是否为机场或火车站（仅限城际交通枢纽，如"深圳宝安机场"、"北京西站"、"广州南站"等）。
   **注意：城市内地铁站（如"下沙地铁站"、"世界之窗地铁站"等）不属于城际车站，不要标记为机场/车站。**
4. 输出格式为 JSON 数组，每条记录包含以下字段：
   - date: 日期 (YYYY-MM-DD 格式)
   - time: 时间 (HH:MM 格式)
   - origin: 起点
   - destination: 终点
   - amount: 金额（数字，保留两位小数）
   - is_airport_or_station: 布尔值，仅当起点或终点为机场或城际火车站时为 true
5. 如果信息模糊或无法确定，合理推断并标注
6. 只需输出 JSON 数组，不要添加其他文字说明"""

STAGE2_PROMPT = """你是一个差旅报销分析专家。请根据以下所有行程条目，进行合并、去重、排序、
区分单次差旅，并计算报销费用。

## 区分差旅的规则

一次完整的差旅由"往返机场/车站"标记起始和结束：
- 行程起点为"机场/车站" = 从机场/车站出发前往目的城市（差旅开始）
- 行程终点为"机场/车站" = 从目的城市返回机场/车站（差旅结束）
- 两次往返机场/车站之间的行程 = 同一趟差旅中的目的城市交通

## 任务

1. 去除完全重复的行程条目
2. 按日期和时间排序所有条目
3. 识别往返机场/车站的行程，以此区分单次出差
4. 为每次出差计算：
   - 起始日期
   - 结束日期
   - 天数（含首尾，如2月10日-2月14日=5天）
   - 目的城市
   - 往返机场/车站费用（求和）
   - 目的城市交通费（求和，即非机场/车站的行程）
   - 单次差旅费用
5. 汇总所有差旅的往返机场/车站费用总计、目的城市交通费总计、总报销金额

## 输出格式

请严格按照以下 Markdown 表格格式输出（行为字段，列为各次差旅+总计列），不要添加额外说明。
N 为实际差旅次数，依此类推。

| | 差旅信息-01 | 差旅信息-02 | ... | 差旅信息-N | 总计费用 |
|---|---|---|---|---|---|
| 起始日期 | YYYY-MM-DD | YYYY-MM-DD | ... | YYYY-MM-DD | |
| 完成日期 | YYYY-MM-DD | YYYY-MM-DD | ... | YYYY-MM-DD | |
| 天数 | N | N | ... | N | |
| 目的城市 | 城市名 | 城市名 | ... | 城市名 | |
| 往返机场/车站费用 | XX.XX | XX.XX | ... | XX.XX | XX.XX |
| 目的城市交通费用 | XX.XX | XX.XX | ... | XX.XX | XX.XX |
| 单次差旅费用 | XX.XX | XX.XX | ... | XX.XX | XX.XX |

以下是所有行程条目：

{all_entries}"""


# ── 数据模型 ──────────────────────────────────────────────────


@dataclass
class PageImage:
    """内存中的页面图片，无需临时文件。"""
    name: str
    data_url: str
    size_bytes: int

    @property
    def size_kb(self) -> float:
        return self.size_bytes / 1024


@dataclass
class PdfTextInput:
    """从文字型 PDF 直接提取的文字内容。"""
    name: str
    text: str


@dataclass
class TripEntry:
    """单条行程记录。"""
    date: str
    time: str
    origin: str
    destination: str
    amount: float
    is_airport_or_station: bool
    source_file: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any], source_file: str = "") -> "TripEntry":
        return cls(
            date=d["date"],
            time=d.get("time", ""),
            origin=d["origin"],
            destination=d["destination"],
            amount=float(d.get("amount", 0)),
            is_airport_or_station=bool(d.get("is_airport_or_station", False)),
            source_file=source_file,
        )

    def __str__(self) -> str:
        tag = "🚗" if not self.is_airport_or_station else "✈️🚄"
        return f"{tag} {self.date} {self.time}  {self.origin} → {self.destination}  ¥{self.amount:.2f}"


# ── PDF → 图片 ────────────────────────────────────────────────

TEXT_MIN_CHARS_PER_PAGE = 200  # 低于此值视为扫描件，降级走图像路径


def pdf_extract_text(pdf_path: Path) -> str | None:
    """尝试从 PDF 提取文字。若所有页面文字量不足，返回 None（视为扫描件）。"""
    import fitz
    doc = fitz.open(str(pdf_path))
    pages_text = [doc[i].get_text() for i in range(doc.page_count)]
    page_count = doc.page_count
    doc.close()
    total_chars = sum(len(t) for t in pages_text)
    if total_chars < TEXT_MIN_CHARS_PER_PAGE * page_count:
        return None
    return "\n".join(pages_text)


def pdf_to_page_images(pdf_path: Path, dpi: int = 85) -> List[PageImage]:
    """将 PDF 每页渲染为 JPEG（DPI=85, quality=70），返回内存 PageImage 列表。

    相比原始 150DPI/PNG + 临时文件，内存模式体积缩小约 60-70%，且无磁盘 I/O。
    """
    import fitz
    doc = fitz.open(str(pdf_path))

    has_pil = False
    try:
        from PIL import Image  # noqa: F401
        has_pil = True
    except ImportError:
        pass

    images: List[PageImage] = []
    for i in range(doc.page_count):
        page = doc[i]
        pix = page.get_pixmap(dpi=dpi)
        buf = io.BytesIO()
        if has_pil:
            pix.pil_save(buf, format="JPEG", quality=70)
            mime = "image/jpeg"
        else:
            pix.save(buf, output="png")
            mime = "image/png"
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        images.append(PageImage(
            name=f"{pdf_path.stem}_p{i+1}",
            data_url=f"data:{mime};base64,{b64}",
            size_bytes=len(buf.getvalue()),
        ))
    doc.close()
    return images


def file_to_page_image(path: Path) -> PageImage:
    """将本地图片文件读取为内存 PageImage。"""
    ext = path.suffix.lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "bmp": "image/bmp", "gif": "image/gif"}.get(ext, "image/png")
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return PageImage(
        name=path.name,
        data_url=f"data:{mime};base64,{b64}",
        size_bytes=len(data),
    )


# ── 解析输入文件 ──────────────────────────────────────────────

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
PDF_EXTENSIONS = {".pdf"}


def resolve_inputs(paths: List[str]):
    """将输入路径解析为文字输入列表和图像输入列表。

    文字型 PDF → PdfTextInput；扫描件 PDF / 图片文件 → PageImage。
    返回 (text_inputs, page_images)。
    """
    text_inputs: List[PdfTextInput] = []
    page_images: List[PageImage] = []

    for raw in paths:
        p = Path(raw).expanduser().resolve()
        if not p.exists():
            print(f"[警告] 文件不存在，跳过: {p}", file=sys.stderr)
            continue

        if p.suffix.lower() in IMAGE_EXTENSIONS:
            page_images.append(file_to_page_image(p))
            print(f"[图片] {p.name}", file=sys.stderr)
        elif p.suffix.lower() in PDF_EXTENSIONS:
            text = pdf_extract_text(p)
            if text:
                text_inputs.append(PdfTextInput(name=p.name, text=text))
                print(f"[PDF/文字] {p.name} → 直接文字提取", file=sys.stderr)
            else:
                imgs = pdf_to_page_images(p)
                page_images.extend(imgs)
                print(f"[PDF/扫描] {p.name} → {len(imgs)} 页图像", file=sys.stderr)
        else:
            print(f"[警告] 不支持的文件格式，跳过: {p}", file=sys.stderr)

    return text_inputs, page_images


# ── Stage 1: 提取行程条目 ─────────────────────────────────────


def _process_text_input(
    text_input: PdfTextInput,
    llm: LLMService,
) -> List[TripEntry]:
    """处理文字型 PDF：直接将文字送给 base_model 解析，返回行程条目。"""
    print(f"  [文字] {text_input.name}  ({len(text_input.text)} 字符)", file=sys.stderr)
    prompt = STAGE1_TEXT_PROMPT.format(text=text_input.text)

    backoff = [5, 15, 30]
    for attempt, wait in enumerate(backoff, 1):
        try:
            response = llm.generate(
                prompt,
                route_context={
                    "input_type": "text",
                    "task_type": "analysis",
                    "complexity": "standard",
                    "use_case": "analysis",
                },
            )
            entries = _parse_stage1_output(response.text, source_file=text_input.name)
            return entries
        except LLMProviderError as exc:
            print(f"   尝试 {attempt}/{len(backoff)} 失败: {exc}", file=sys.stderr)
            if attempt < len(backoff):
                print(f"   等待 {wait}s 后重试...", file=sys.stderr)
                time.sleep(wait)

    print(f"  [跳过] 无法处理该文件（已重试 {len(backoff)} 次）", file=sys.stderr)
    return []


def _process_single_page(
    page_img: PageImage,
    llm: LLMService,
) -> List[TripEntry]:
    """处理单张图片：调用多模态 API（含指数退避重试），返回行程条目。"""
    print(f"  [图片] {page_img.name}  ({page_img.size_kb:.0f} KB)", file=sys.stderr)
    content_parts = [
        {"type": "text", "text": STAGE1_PROMPT},
        {"type": "image_url", "image_url": {"url": page_img.data_url}},
    ]

    backoff = [5, 15, 30]  # 指数退避：5s → 15s → 30s
    for attempt, wait in enumerate(backoff, 1):
        try:
            text = llm.generate_multimodal(
                content_parts,
                route_context={
                    "input_type": "multimodal",
                    "task_type": "image_understanding",
                    "complexity": "complex",
                    "use_case": "image_understanding",
                },
            )
            entries = _parse_stage1_output(text, source_file=page_img.name)
            return entries
        except LLMProviderError as exc:
            print(f"   尝试 {attempt}/{len(backoff)} 失败: {exc}", file=sys.stderr)
            if attempt < len(backoff):
                print(f"   等待 {wait}s 后重试...", file=sys.stderr)
                time.sleep(wait)

    print(f"  [跳过] 无法处理该页面（已重试 {len(backoff)} 次）", file=sys.stderr)
    return []


def stage1_extract_entries(
    llm: LLMService,
    text_inputs: List[PdfTextInput],
    page_images: List[PageImage],
) -> List[TripEntry]:
    """提取所有行程条目：文字型 PDF 走文本路径，图像型走多模态路径（并发）。"""
    all_entries: List[TripEntry] = []

    # 文字型 PDF：串行处理（通常只有少数几个文件）
    for text_input in text_inputs:
        entries = _process_text_input(text_input, llm)
        print(f"  ✓ {text_input.name} → {len(entries)} 条行程", file=sys.stderr)
        all_entries.extend(entries)

    # 图像型：并发处理
    if page_images:
        max_workers = min(2, len(page_images))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            fut_map = {
                executor.submit(_process_single_page, img, llm): img
                for img in page_images
            }
            for fut in as_completed(fut_map):
                img = fut_map[fut]
                try:
                    entries = fut.result()
                    all_entries.extend(entries)
                    print(f"  ✓ {img.name} → {len(entries)} 条行程", file=sys.stderr)
                except Exception as exc:
                    print(f"  ✗ {img.name} 处理异常: {exc}", file=sys.stderr)

    all_entries.sort(key=lambda x: (x.date, x.time))
    return all_entries


def _parse_stage1_output(text: str, source_file: str = "") -> List[TripEntry]:
    """从 adv_model 输出中解析 JSON 数组。"""
    # 尝试提取 JSON 数组
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1:
        json_str = text[start:end + 1]
        try:
            data = json.loads(json_str)
            if isinstance(data, list):
                return [TripEntry.from_dict(item, source_file) for item in data]
        except json.JSONDecodeError:
            pass

    print(f"  [警告] 无法解析 adv_model 输出 (源: {source_file})", file=sys.stderr)
    print(f"    原始输出: {text[:200]}", file=sys.stderr)
    return []


# ── Stage 2: base_model 整合 ─────────────────────────────────


def stage2_consolidate(
    llm: LLMService,
    entries: List[TripEntry],
) -> str:
    """调用 base_model 合并、排序、区分差旅并计算报销。"""
    lines = []
    for i, e in enumerate(entries, 1):
        lines.append(f"{i}. [{e.date} {e.time}] {e.origin} → {e.destination}  "
                     f"¥{e.amount:.2f}  "
                     f"{'【机场/车站】' if e.is_airport_or_station else '【市内】'}  "
                     f"来源: {e.source_file}")
    all_entries_text = "\n".join(lines)

    prompt = STAGE2_PROMPT.format(all_entries=all_entries_text)

    backoff = [2, 4, 8]
    for attempt, wait in enumerate(backoff, 1):
        try:
            response = llm.generate(
                prompt,
                route_context={
                    "input_type": "text",
                    "task_type": "analysis",
                    "complexity": "standard",
                    "use_case": "analysis",
                },
            )
            return response.text.strip()
        except LLMProviderError as exc:
            print(f"  [重试 {attempt}/{len(backoff)}] base_model 失败: {exc}", file=sys.stderr)
            if attempt < len(backoff):
                print(f"   等待 {wait}s 后重试...", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError("base_model 调用失败，已重试 3 次")


# ── CLI ────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="网约车行程单差旅报销信息提取")
    parser.add_argument("files", nargs="+", help="行程单文件路径（PDF/图片）")
    parser.add_argument("--output", "-o", help="输出文件路径（可选）")
    args = parser.parse_args()

    # 加载配置
    config = load_config_bundle(PROJECT_ROOT)
    llm = LLMService(config)

    # 解析输入文件（文字型 PDF 直接提取文字，扫描件/图片转内存图像）
    print("=" * 50, file=sys.stderr)
    print("网约车行程单差旅报销提取", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    text_inputs, page_images = resolve_inputs(args.files)
    if not text_inputs and not page_images:
        print("[错误] 没有可处理的文件", file=sys.stderr)
        sys.exit(1)
    if page_images:
        print(f"\n待分析图片: {len(page_images)} 张", file=sys.stderr)
        total_kb = sum(p.size_kb for p in page_images)
        print(f"图片总大小: {total_kb:.0f} KB", file=sys.stderr)

    # Stage 1: 提取行程条目
    print("\n[Stage 1] 提取行程条目...", file=sys.stderr)
    entries = stage1_extract_entries(llm, text_inputs, page_images)
    if not entries:
        print("[错误] 未提取到任何行程条目", file=sys.stderr)
        sys.exit(1)
    print(f"\n共提取 {len(entries)} 条行程条目", file=sys.stderr)

    # 预览
    print("\n行程条目预览:", file=sys.stderr)
    for e in sorted(entries, key=lambda x: (x.date, x.time)):
        print(f"  {e}", file=sys.stderr)

    # Stage 2: base_model 整合
    print("\n[Stage 2] base_model 整合分析...", file=sys.stderr)
    try:
        result = stage2_consolidate(llm, entries)
    except RuntimeError as exc:
        print(f"\n[错误] {exc}", file=sys.stderr)
        sys.exit(1)

    # 输出
    print("\n" + "=" * 50, file=sys.stderr)
    print(result)

    if args.output:
        out_path = Path(args.output)
    else:
        # 自动生成文件名：最早起始日期 ~ 最晚结束日期
        dates = [e.date for e in entries if e.date]
        if dates:
            from_date, to_date = min(dates), max(dates)
        else:
            from_date = to_date = "unknown"
        out_path = PROJECT_ROOT / "output" / f"差旅报销汇总-{from_date}~{to_date}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result, encoding="utf-8")
    print(f"\n结果已保存到: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
