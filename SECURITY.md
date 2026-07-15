# 安全政策

## 报告漏洞

如果你发现 Iris 项目中的安全漏洞，请通过以下方式**私密报告**：

📧 在 GitHub 上使用 **"Report a vulnerability"** 功能（Security Advisories）

请不要通过公开 Issue 报告安全漏洞。

## 期望

- 我们将在 **5 个工作日内** 确认收到报告
- 我们将在 **30 天内** 提供修复或缓解方案
- 在修复发布前，请勿公开披露漏洞细节

## 安全实践

Iris 项目遵循以下安全实践：

### API 密钥管理

- 所有 API 密钥通过环境变量或 macOS Keychain 管理
- `.env` 和 `config/*.json` 文件已在 `.gitignore` 中排除
- 项目提供的 `.example` 文件使用占位符，不含真实凭证

### 文件路径安全

- 数据源和 Wiki 输出路径通过环境变量 `${IRIS_WORK_DOCS_DIR}` 和 `${IRIS_WIKI_ROOT}` 配置
- 飞书文档图片下载在配置文件指定的目录范围内执行，防止路径逃逸
- 临时文件和中间结果存储在项目 `data/` 目录下（gitignored）

### LLM 调用安全

- 错误消息和日志输出经过脱敏处理，不包含完整的 Markdown 内容
- LLM 响应缓存仅保存 hash 索引，不暴露原始查询内容

### 依赖安全

建议定期检查依赖安全更新：

```bash
pip list --outdated
pip-audit  # 如安装
```

## 支持的版本

| 版本 | 安全补丁支持 |
|------|:---:|
| 最新版本 (main) | ✅ |
| 旧版本 | ❌ |

请始终使用最新版本。

## 致谢

我们感谢所有通过私密渠道负责任地报告安全问题的研究人员和贡献者。

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
