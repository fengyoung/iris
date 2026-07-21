# 贡献指南

感谢你对 Iris 的关注！Iris 是一个工作知识助手，帮助个人和团队管理 Obsidian Wiki 知识库并与飞书集成。

## 如何贡献

### 报告问题

- 使用 GitHub Issues 报告 bug
- 清晰描述问题：预期行为 vs 实际行为
- 提供复现步骤和环境信息（Python 版本、操作系统）
- 不要提交包含个人身份信息（PII）或 API 密钥的内容

### 提交代码

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 编写代码 + 测试
4. 确保所有测试通过：`python -m pytest tests/ -q`
5. 提交变更：`git commit -m "feat: 添加 XXX 功能"`
6. 推送分支并创建 Pull Request

### 提交规范

采用约定式提交（Conventional Commits）：

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `refactor` | 重构（无功能变更） |
| `docs` | 文档更新 |
| `test` | 测试相关 |
| `chore` | 构建/工具/依赖 |

### 代码风格

- 遵循已有代码风格
- 注释和文档使用中文
- 新增功能需包含单元测试
- 保持 Python 3.9+ 兼容性

### 开发环境

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 安装 pre-commit hooks（可选，推荐）
pre-commit install

# 快速命令（Makefile）
make test            # 运行全部测试（1,829 用例，103 文件）
make test-unit       # 纯逻辑单元测试（0.5s 快速反馈）
make test-integration # 集成测试
make test-cov        # 运行测试 + 覆盖率报告
make lint            # Ruff 代码检查
make lint-fix        # Ruff 自动修复
make format          # 代码格式化
make clean           # 清理缓存

# 或直接使用 pytest
python -m pytest tests/ -q
python -m pytest tests/ -q --cov=iris --cov-report=term
```

### 安全注意事项

- 不要在代码、注释或提交信息中包含 API 密钥、密码或个人身份信息
- 本地配置文件（`.env`、`config/*.json`、`.claude/settings.*`）已在 `.gitignore` 中排除
- 如发现安全漏洞，请参考 [SECURITY.md](SECURITY.md) 的私密报告流程

## 行为准则

本项目遵循贡献者公约。参与即表示你同意遵守其条款。

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
