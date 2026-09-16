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
- 保持 Python 3.11+ 兼容性，并在 3.11、3.12、3.13 上通过 CI

### 开发环境

```bash
# 安装开发依赖（推荐使用约束文件确保可复现构建）
make install-dev     # 等同于 pip install -e ".[dev]" -c constraints.txt
# 或手动安装
pip install -e ".[dev]" -c constraints.txt

# 安装 pre-commit hooks（可选，推荐）
pre-commit install

# 快速命令（Makefile）
make test            # 运行全部测试（3,538 用例）
make test-unit       # unit 标记测试（含部分历史 I/O 测试）
make test-integration # 集成测试
make test-cov        # 运行测试 + 覆盖率报告
make lint            # Ruff 代码检查
make lint-fix        # Ruff 自动修复
make format          # 代码格式化
make audit           # 依赖安全审计
python scripts/security_scan.py  # AST 安全静态扫描
python scripts/generate_sbom.py --output dist/iris.spdx.json  # 生成 SPDX SBOM
make typecheck       # mypy 类型检查（CI 门禁；显式兼容边界见 pyproject.toml）
make clean           # 清理缓存

# 或直接使用 pytest
python -m pytest tests/ -q
python -m pytest tests/ -q --cov=iris --cov-report=term
```

### 数据与 HTTP 回归门禁（v3.40.0）

- 全局覆盖率下限 65%；`scripts/check_coverage_thresholds.py` 另检查 Web、复盘、向量、缓存等关键模块。
- 路径/HTTP 测试必须覆盖目录穿越、符号链接、非法 JSON、跨站请求及资源配额。
- 持久化测试覆盖读写并发、陈旧写者、部分发布失败以及连续新增/修改/删除。
- 新增检索黄金集使用 `scripts/evaluate_retrieval.py`；用已标注 chunk ID 评估召回、引用完整性和过期证据。
- 类型检查未启用全局 `strict`，当前 17 个历史模块保留显式豁免；新增模块不得扩大豁免，本版已移除 `retrieval.enhanced` 的豁免。

### 安全注意事项

- 不要在代码、注释或提交信息中包含 API 密钥、密码或个人身份信息
- 本地配置文件（`.env`、`config/*.json`、`.claude/settings.*`）已在 `.gitignore` 中排除
- 如发现安全漏洞，请参考 [SECURITY.md](SECURITY.md) 的私密报告流程

### 发布流程

每次版本发布需执行以下步骤：

1. 更新 `pyproject.toml` 中的 `version` 字段
2. 更新 `src/iris/__init__.py` 中的 `__version__`（仅在 CLI 命令集变更时）
3. 更新 `CHANGELOG.md`，记录本版本的全部变更
4. 完成安全扫描和 SPDX SBOM 生成，并确认分层覆盖率在合并后通过全局与关键模块门禁
5. 提交变更：`git commit -m "chore: 升级产品版本 X.Y.Z → X.Y.Z+1"`

涉及持久化或并发逻辑时，同时检查 [工程可靠性设计](docs/engineering-reliability-design.md) 中的锁、原子发布和资源生命周期约定。
6. 打 tag：`git tag -a vX.Y.Z+1 -m "Iris X.Y.Z+1"`
7. 推送 tag：`git push origin vX.Y.Z+1`
8. 在 GitHub Releases 页面基于 tag 创建 Release

> 注意：打 tag 前确保所有 CI 检查通过（`make all`）。

## 行为准则

本项目遵循贡献者公约。参与即表示你同意遵守其条款。

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
