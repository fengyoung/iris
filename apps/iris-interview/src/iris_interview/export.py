"""Project report export; plain Markdown suitable for copying into hiring forms."""

from pathlib import Path
from .preferences import atomic_json


def evaluation_markdown(project: dict) -> str:
    evaluation = project.get("evaluation", {})
    draft = evaluation.get("draft", {})
    lines = [
        f"# {project.get('name') or '面试评价'}",
        "",
        f"候选人：{project.get('candidate', '')}  岗位：{project.get('job', '')}",
        "",
        "## 过程总结",
        draft.get("summary", ""),
        "",
        "## 领导力评估",
    ]
    for row in draft.get("leadership", []):
        lines.extend(
            [
                f"### {row['dimension']}：{row.get('rating', '未考察')}",
                row.get("reason", ""),
                "证据：" + ", ".join(row.get("evidence", [])),
                "",
            ]
        )
    skills = draft.get("skills", {})
    lines += ["## 专项技能及个人情况", f"专业匹配：{skills.get('match', '待补充')}"]
    for key, label in [
        ("reason", "匹配依据"),
        ("motivation", "求职动机"),
        ("strengths", "优势与短板"),
        ("risks", "风险"),
        ("ai_ability", "AI 能力"),
    ]:
        lines.extend([f"### {label}", skills.get(key, ""), ""])
    lines += [
        "## 面试结论",
        draft.get("result", "待定"),
        draft.get("conclusion", ""),
        f"建议职级：{evaluation.get('suggested_level') or '未填写'}",
        "",
        "## 转写证据",
    ]
    refs = {r for row in draft.get("leadership", []) for r in row.get("evidence", [])}
    refs.update(skills.get("evidence", []))
    for segment in project.get("transcript", []):
        if segment["id"] in refs:
            lines.append(
                f"- {segment['id']} [{segment.get('start', 0):.1f}s] {segment.get('speaker', '')}："
                + segment.get("manual", segment.get("corrected", segment.get("raw", "")))
            )
    return "\n".join(lines) + "\n"


def export_report(project: dict, path: Path) -> None:
    if path.suffix == ".json":
        atomic_json(path, project.get("evaluation", {}))
    else:
        path.write_text(evaluation_markdown(project), encoding="utf-8")
