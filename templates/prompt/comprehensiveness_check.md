你正在审核 Wiki 页面是否遗漏了源文档中的关键信息。

【Wiki 页面标题】
{{wiki_title}}

【Wiki 页面核心内容】
{{wiki_content_snippet}}

【候选源文档片段】
{{candidate_source}}

请判断：候选源文档中是否包含与 Wiki 主题相关、但 Wiki 页面未覆盖的关键信息？
- has_gap: 候选源中有关键信息遗漏
- no_gap: 候选源的内容已被 Wiki 覆盖，或与主题不直接相关

仅输出一行，格式：判定结果|简要说明遗漏了什么
示例：no_gap|已覆盖
示例：has_gap|提到了 XXX 的具体时间节点和责任人，Wiki 未收录