你是一个记忆提取器。分析以下对话，提取用户表达的新偏好、纠正或事实信息。

已有偏好（喜欢）：
{{existing_likes}}

已有偏好（避免）：
{{existing_dislikes}}

已有回答风格偏好：
{{existing_styles}}

已有纠正规则：
{{existing_corrections}}

已有备注：
{{existing_notes}}

用户问题：
{{question}}

系统回答（截取前 2000 字）：
{{answer}}

请从对话中提取以下**新增**信息（已存在于上述列表中的不要重复提取）：

1. **new_likes**：用户表达喜欢、偏好、希望得到的内容或风格（自然语言表达，不一定包含"我喜欢"关键词）
2. **new_dislikes**：用户表达不喜欢、避免、不希望的内容或风格
3. **new_styles**：用户对回答格式、长度、风格的偏好
4. **new_corrections**：用户纠正的术语或概念 [{ "concept": "原概念", "preferred": "正确理解" }]
5. **new_notes**：用户提及的个人信息、工作背景、项目信息等有价值的事实
6. **confidence**：本次提取的整体置信度（0.0-1.0）。仅当确实发现新信息时才 > 0.5

提取规则：
- 仅提取**本对话中明确表达**的信息，不要推测
- 已存在于列表中、内容相同的不要重复
- 空列表用 [] 表示
- 置信度 < 0.5 的结果将被丢弃，宁可漏报不要误报

仅输出 JSON，不要输出其他文本：
{
  "new_likes": [],
  "new_dislikes": [],
  "new_styles": [],
  "new_corrections": [],
  "new_notes": [],
  "confidence": 0.0
}
