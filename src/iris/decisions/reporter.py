"""可溯源的决策 Markdown 报告。"""
def render_report(records: list[dict]) -> str:
    lines = ['# 决策报告', '']
    for row in sorted(records, key=lambda r: (r['decided_at'], r['decision_id'])):
        lines.extend([f"## {row['decided_at']} · {row['title']}",
                      f"编号：{row['decision_id']} | 状态：{row['status']} | 负责人：{'、'.join(row['owners']) or '未指定'}",
                      f"背景：{row['context']}", f"结论：{row['outcome']}", f"依据：{row['rationale']}",
                      '关联 KR：' + '、'.join(row['related_krs']),
                      '关联决策：' + '、'.join(row['related_decisions'])])
        lines.extend(f"- 来源：{s['path']}（第 {s.get('line_start', 0)} 行）\n  > {s.get('quote', '')}" for s in row['sources'])
        lines.append('')
    return '\n'.join(lines)
