.PHONY: test test-unit test-integration test-cov lint lint-fix format typecheck clean audit install-dev push pull all

# 运行全部测试
test: test-unit test-integration

# 纯逻辑单元测试（10 秒内完成）
test-unit:
	pytest tests/ -m unit -x --tb=short -q

# 集成测试（含 I/O / mock）
test-integration:
	pytest tests/ -m integration -x --tb=short -q

# 运行测试 + 覆盖率报告
test-cov:
	pytest tests/ --cov=src/iris --cov-report=term --cov-report=html -x --tb=short

# Ruff 代码检查（规则由 pyproject.toml 统一管理）
lint:
	ruff check src scripts tests

# Ruff 自动修复
lint-fix:
	ruff check --fix src scripts tests

# 代码格式化
format:
	ruff format src scripts tests

# 静态类型检查（CI 门禁；动态/平台专属模块的兼容边界见 pyproject [tool.mypy]）
typecheck:
	mypy src/iris --no-incremental

# 依赖安全审计（需安装 pip-audit: pip install pip-audit）
audit:
	pip-audit

# 安装开发依赖（使用约束文件确保可复现构建）
install-dev:
	pip install -e ".[dev]" -c constraints.txt

# 双远端同步（origin=github / gitee，两者互为镜像）
# 推/拉当前分支；`&&` 串联失败即停，避免只同步了一边而另一边悄悄落后
push:
	@b=$$(git rev-parse --abbrev-ref HEAD); \
	if [ "$$b" = "HEAD" ]; then echo "detached HEAD：请先切到分支"; exit 1; fi; \
	git push origin $$b && git push gitee $$b

# --ff-only：只允许快进，绝不自动造 merge 提交；镜像分叉时明确失败而非掩盖
pull:
	@b=$$(git rev-parse --abbrev-ref HEAD); \
	if [ "$$b" = "HEAD" ]; then echo "detached HEAD：请先切到分支"; exit 1; fi; \
	git pull --ff-only origin $$b && git pull --ff-only gitee $$b

# 清理缓存
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .coverage .ruff_cache

# 全量检查
all: lint audit test-cov
