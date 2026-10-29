---
name: iris-release
version: 1.0.2
description: Iris 发版与提交 — 自动完成提交、版本号同步、门禁检查、打 tag、双远端推送与 GitHub Release。当用户说「发版」「发布版本」「发布并提交」「提交并推送」「合并分支」或指定 /iris-release 时使用。
user-invocable: true
metadata:
  requires:
    bins: ["git", "make", "python3"]
---

# Iris 发版与提交

> 所有命令在 iris3 项目根目录执行（cwd = 仓库根目录）。

## 授权语义

用户说「发版 / 发布版本 / 发布并提交」即视为授权以下全部动作，**不要反复确认**：

1. 提交工作区改动（无改动则跳过代码提交）
2. 同步 5 个版本文件
3. 跑完整门禁
4. 打附注 tag
5. 推送 `origin`（GitHub）+ `gitee`
6. 创建 GitHub Release

只有以下情况必须停下来问用户：

- 推断为 **major**，但用户没有明确说 major / 大版本 / `X.0.0`
- 工作区有与当前任务无关的改动、敏感文件或大型未跟踪文件
- 任一门禁失败
- 远端分叉、tag 已存在、CHANGELOG 条目已存在
- `AGENTS.md` 不是指向 `CLAUDE.md` 的软链
- 协议版本是否需要递增无法判断

其余情况直接执行，最后给出回执。

## 模式

| 用户说法 | 模式 | 行为 |
|---|---|---|
| 发布 / 发版 / 发布并提交 | `release` | 全流程 |
| 发布 minor / 发布 patch / 发布 3.44.0 | `release` | 覆盖自动推断 |
| 提交 / commit | `commit` | 只提交，不动版本、不打 tag、不推送 |
| 提交并推送 | `commit-push` | 提交 + `make push`，不发版 |
| 合并 `<分支>` | `merge` | `--no-ff` 合并到 main + 门禁 + `make push` |
| 预览发布 / dry-run | `dry-run` | 只输出推断版本、待改文件、门禁计划，不写任何文件 |

执行前先根据用户说法确定 `MODE`（`release|commit|commit-push|merge|dry-run`），
后续命令中的 `$MODE` 即指此值。

## 0. 解释器与前置检查

本机系统 `python3` 可能是 3.9，必须使用 3.11+：

```bash
PY="${IRIS_PYTHON:-python3}"
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
  for c in python3.13 python3.12 python3.11 \
           "$HOME/miniforge3/bin/python3" "$HOME/miniconda3/bin/python3"; do
    if command -v "$c" >/dev/null 2>&1 \
       && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  done
fi
"$PY" -c 'import sys; assert sys.version_info >= (3,11), sys.version' || {
  echo "需要 Python 3.11+；可设置 IRIS_PYTHON"; exit 1; }

git rev-parse --show-toplevel >/dev/null 2>&1 || { echo "不在 git 仓库"; exit 1; }
[ -f pyproject.toml ] && [ -f scripts/run_cli.py ] || { echo "不在 iris3 项目根目录"; exit 1; }
[ -L AGENTS.md ] && [ "$(readlink AGENTS.md)" = "CLAUDE.md" ] || {
  echo "AGENTS.md 不是指向 CLAUDE.md 的软链，停止"; exit 1; }

git remote get-url origin >/dev/null 2>&1 || { echo "缺少 origin 远端"; exit 1; }
git remote get-url gitee  >/dev/null 2>&1 || { echo "缺少 gitee 远端"; exit 1; }
```

发版必须在 `main` 分支（本仓库历史约定）：

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
[ "$BRANCH" = "main" ] || [ "$MODE" = "merge" ] || {
  echo "当前分支 $BRANCH，不是 main；先合并或切换"; exit 1; }
```

发版前先同步双远端（只允许快进，分叉就停）：

```bash
make pull
```

`make pull` 失败（分叉/本地提交无法快进）时**停止**，不要 rebase、不要 merge、不要 force。

## 1. 检查当前版本一致性

```bash
CURRENT=$("$PY" -c "import tomllib,pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])")
grep -q "^# Iris $CURRENT " CLAUDE.md   || { echo "CLAUDE.md 标题版本不一致"; exit 1; }
grep -q "^# Iris $CURRENT" README.md     || { echo "README.md 标题版本不一致"; exit 1; }
grep -qF "| $CURRENT |" CLAUDE.md         || { echo "版本体系表不一致"; exit 1; }
```

不一致说明上一次发版没收口，先修一致再继续。

## 2. 检查工作区并暂存（先不提交）

```bash
git status --short
git diff --stat
```

- 若出现与当前任务无关的文件：停止，列出文件并询问。
- 检查敏感/生成文件是否被暂存：

```bash
git status --porcelain | grep -E '(^|/)(\.env|config/.*\.json|data/|dist/|build/)|\.(pem|key|p12)$' \
  && { echo "发现敏感/生成文件，停止"; exit 1; } || true
```

- `git status` 干净 → 本次没有代码提交，`HAS_CODE_CHANGE=0`。
- 有改动 → 暂存：

```bash
HAS_CODE_CHANGE=1
git add -u
for p in src tests scripts docs .claude/skills config; do
  [ -e "$p" ] && git add "$p"
done
git add README.md CHANGELOG.md CLAUDE.md pyproject.toml 2>/dev/null || true
git status --short
```

如安装了 pre-commit，先跑一遍并重新暂存（它会格式化文件）：

```bash
if "$PY" -m pre_commit --version >/dev/null 2>&1; then
  "$PY" -m pre_commit run --all-files
  git add -u
fi
```

## 3. 决定提交类型与版本

### 3.1 选择提交类型

根据本次改动选择 **一个** 类型：

| 改动性质 | 类型 |
|---|---|
| 新功能 / 能力增强 | `feat` |
| Bug 修复 | `fix` |
| 重构（无行为变化） | `refactor` |
| 只有文档 | `docs` |
| 只有测试 | `test` |
| 构建 / 工具 / 依赖 | `chore` |
| 破坏性变更 | `feat!` 或 `fix!`，并标记 `BREAKING=1` |

没有代码改动时 `TYPE` 可为空。

### 3.2 推断 bump

本仓库历史版本**没有打 tag**（只有 `archive/...` 分支标签），因此以最近一次
`release: vX.Y.Z` 提交为基线，不要用 `git describe`：

```bash
LAST_RELEASE=$(git log --grep='^release: v[0-9]' --format='%H' -1)
RANGE="${LAST_RELEASE:+$LAST_RELEASE..HEAD}"
COMMITS=$(git log --pretty=format:'%s%n%b' $RANGE)

DIRTY=$(git status --porcelain | wc -l | tr -d ' ')
if [ "${HAS_CODE_CHANGE:-0}" = "0" ] && [ "$DIRTY" = "0" ] && [ -z "$(git log --oneline $RANGE)" ]; then
  echo "自上次 release 提交以来没有新提交，也没有工作区改动；无需发版"; exit 1
fi

if [ "${BREAKING:-0}" = "1" ] \
   || printf '%s\n' "$COMMITS" | grep -qE '^[a-z]+(\(.+\))?!:|BREAKING CHANGE'; then
  BUMP=major
elif [ "$TYPE" = "feat" ] \
     || printf '%s\n' "$COMMITS" | grep -qE '^feat(\(.+\))?:'; then
  BUMP=minor
else
  BUMP=patch
fi
```

用户指定 `发布 minor` / `发布 patch` / `发布 3.44.0` 时覆盖 `BUMP`。
**推断为 major 但用户未明确说 major / 大版本 / X.0.0 时，停止询问。**

### 3.3 计算并校验版本

```bash
NEW=$("$PY" - "$CURRENT" "$BUMP" <<'PY'
import sys
cur, bump = sys.argv[1], sys.argv[2]
major, minor, patch = map(int, cur.split('.'))
if bump == 'major':   print(f'{major+1}.0.0')
elif bump == 'minor': print(f'{major}.{minor+1}.0')
else:                 print(f'{major}.{minor}.{patch+1}')
PY
)

git rev-parse -q --verify "refs/tags/v$NEW" && { echo "tag v$NEW 已存在"; exit 1; }
grep -q "^## v$NEW " CHANGELOG.md && { echo "CHANGELOG 已有 v$NEW"; exit 1; }
echo "版本推断：$CURRENT → $NEW（$BUMP）"
```

## 4. 跑完整门禁

按顺序执行，任何一步失败立即停止（**不提交、不打 tag、不推送**）：

```bash
"$PY" -m ruff check src scripts tests
"$PY" -m mypy src/iris --no-incremental
"$PY" scripts/security_scan.py
"$PY" -m pip wheel . --no-deps --no-build-isolation -w dist
"$PY" scripts/generate_sbom.py --output dist/iris.spdx.json
"$PY" -m pytest tests/ -q --cov=src/iris --cov-report=term --cov-report=json:coverage.json \
  > /tmp/iris-release-tests.log 2>&1
cat /tmp/iris-release-tests.log
"$PY" scripts/check_coverage_thresholds.py coverage.json
"$PY" scripts/run_cli.py --help >/dev/null
```

### pip-audit 门禁：区分项目依赖与本地环境

本机 miniforge/conda 环境会扫描到大量与项目无关的包（aiohttp / pillow / yt-dlp 等），
直接 `pip_audit` 会误报。依赖未变化时，以最近一次 main 绿色 CI 的依赖审计为准；
依赖变化时必须在 clean venv / CI 中重新审计：

```bash
# commit / commit-push 跳过第 3 步时也要能独立判断基线。
[ -n "$LAST_RELEASE" ] || LAST_RELEASE=$(git log --grep='^release: v[0-9]' --format='%H' -1)
if [ -z "$LAST_RELEASE" ]; then
  echo "无历史 release 基线，无法判断依赖是否变化；请在 clean venv/CI 中审计"; exit 1
fi

DEPS_DIFF=$(git diff "$LAST_RELEASE" -- pyproject.toml constraints.txt \
  | grep -E '^[+-]' | grep -vE '^[+-]{3}|^[+-]version =|^[+-]$' || true)

if [ -n "$DEPS_DIFF" ]; then
  echo "检测到依赖变更，必须在 clean venv / CI 中完成 pip-audit："
  echo "$DEPS_DIFF"
  exit 1
fi

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  CI_CONCLUSION=$(gh run list --branch main --workflow ci.yml --limit 1 --json conclusion --jq '.[0].conclusion')
  [ "$CI_CONCLUSION" = "success" ] || {
    echo "依赖未变化，但最近一次 main CI 非绿（${CI_CONCLUSION:-未完成}）；停止"; exit 1; }
  echo "pip-audit：依赖未变化，采用最近一次绿色 CI 的依赖审计结果"
else
  echo "gh 不可用，无法确认 CI 依赖审计；请先在 clean venv 中运行 pip-audit 或等待 CI"; exit 1
fi
```

> 只有依赖文件（`pyproject.toml` 的 dependencies/extras、`constraints.txt`）真正变化时
> 才需要重新审计；单纯版本号变更会被上面的过滤排除。
> `git diff <基线提交>` 对比的是工作区，能看见已暂存和未暂存的依赖改动——依赖门禁
> 跑在第 5 步提交之前，不能写成 `..HEAD`（那样会漏掉本次待提交的依赖变更）。

解析测试数与覆盖率，后续写文档必须用真实值：

```bash
TESTS=$(grep -Eo '[0-9]+ passed' /tmp/iris-release-tests.log | tail -1 | grep -Eo '[0-9]+')
COVERAGE=$("$PY" -c "import json; print(f\"{json.load(open('coverage.json'))['totals']['percent_covered']:.2f}\")")
[ -n "$TESTS" ] && [ -n "$COVERAGE" ] || { echo "无法解析测试数/覆盖率"; exit 1; }
echo "tests=$TESTS coverage=$COVERAGE%"
```

> `coverage.json`、`.coverage`、`dist/` 已在 `.gitignore`，不要提交。
> Python 3.11/3.12/3.13 矩阵与 macOS smoke 由 CI 在推送后跑；本地只跑当前解释器。

## 5. 提交代码

只有 `HAS_CODE_CHANGE=1` 才执行；否则跳过本步。

```text
<type>: <中文摘要>

- <要点 1>
- <要点 2>

测试：全量 TESTS 通过，覆盖率 COVERAGE%；Ruff / mypy / 安全扫描 / SBOM 通过；pip-audit 采用最近绿色 CI 结果。
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

```bash
git commit -m "<type>: <中文摘要>" -m "- <要点 1>
- <要点 2>

测试：全量 $TESTS 通过，覆盖率 $COVERAGE%；Ruff / mypy / 安全扫描 / SBOM 通过；pip-audit 采用最近绿色 CI 结果。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- 摘要由本次改动生成，不要空泛写「更新代码」。
- 提交信息禁止包含密钥、密码、PII。

## 6. 同步 5 个版本文件

### 6.1 `pyproject.toml`

`version = "OLD"` → `version = "NEW"`（唯一匹配）。

### 6.2 `CHANGELOG.md`

在文件顶部新增：

```markdown
## vNEW (YYYY-MM-DD) — <标题>

<本版为特性/修复/文档说明，2-4 句>

- <变更 1>
- <变更 2>
- <变更 3>

产品版本 OLD→**NEW**；协议版本保持 **P**（或 P→P+1），数据版本不变。全量单测 TESTS 通过、覆盖率 COVERAGE%；Ruff（`src scripts tests`）与 mypy 通过。
```

标题与要点来自本次代码提交；不要复制上一条版本内容。

### 6.3 `CLAUDE.md` — 5 个位置

1. **第 1 行标题**：`# Iris NEW — 项目执行说明`
2. **「当前规模」段**：测试数改为 `TESTS`、覆盖率改为 `COVERAGE%`；若行数/文件数/模块数变化明显，一并更新
3. **「最新版本」行**：`**最新版本**（vNEW）：<标题>。详细变更历史见 [CHANGELOG.md](CHANGELOG.md)。`
4. **「版本体系」表产品版本行**：`| **产品版本** | \`pyproject.toml\` | NEW |`
5. **「项目结构」**：`├── tests/ # TESTS 用例（pytest 全量）`

一次性修正：如果「发版版本号同步规则」段仍写「近期能力」，改为
「标题/规模/最新版本行/版本体系表/项目结构用例数」。

### 6.4 `README.md`

- 第 1 行：`# Iris NEW`
- 在 `## 最新动态` 下前置新块，并把原最新块改成 `**上一版 vOLD (日期)**`：

```markdown
**vNEW (YYYY-MM-DD)** - <标题>：
- <要点 1>
- <要点 2>
- <要点 3>
- ✅ **产品版本 OLD → NEW**；协议版本与数据格式不变
```

### 6.5 `AGENTS.md`

软链，不编辑；已在第 0 步校验。

### 6.6 协议版本与数据版本

```bash
PROTO=$("$PY" -c 'import pathlib,re; print(re.search(r"__version__\s*=\s*\"([^\"]+)\"", pathlib.Path("src/iris/__init__.py").read_text()).group(1))')
```

- **协议版本**（`src/iris/__init__.py`，当前 3.27）：只有 `COMMANDS` 命令集或
  agent-spec 能力定义变化时才递增。仅新增参数不算。
  - 递增时同步：`__version__`、CLAUDE.md 版本体系表协议行、CHANGELOG/README 收口句。
- **数据版本**（`config/*.json`，当前 app 3.8）：只有配置 schema 变化且项目惯例要求时
  才递增；默认不变。不要因为新增一个可选配置键就自动改。

校验：

```bash
grep -n "^# Iris $NEW" CLAUDE.md
grep -n "| \*\*产品版本\*\* | .*| $NEW |" CLAUDE.md
grep -n "tests/.*$TESTS 用例" CLAUDE.md
grep -n "^## v$NEW " CHANGELOG.md
grep -n "^# Iris $NEW" README.md
```

## 7. 发布提交

```bash
git add pyproject.toml CHANGELOG.md CLAUDE.md README.md
# 如协议版本递增，再 git add src/iris/__init__.py
git status --short
git commit -m "release: v$NEW — <标题>" -m "- 产品版本 $CURRENT → $NEW
- 协议版本保持 $PROTO（或 $PROTO → $PROTO_NEXT）；数据版本不变
- 门禁：全量单测 $TESTS 通过 / 覆盖率 $COVERAGE% / Ruff / mypy / 安全扫描 / SBOM / pip-audit（CI 证据）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

## 8. 打 tag

```bash
git tag -a "v$NEW" -m "Iris $NEW"
git describe --tags --abbrev=0
```

本仓库历史版本未打 tag；本次只创建 `v$NEW`，**不回溯补旧 tag**。

## 9. 推送双远端 + tag

```bash
make push                       # 当前 main 分支 → origin + gitee
git push origin "v$NEW"
git push gitee  "v$NEW"
```

验证两端都收到：

```bash
git ls-remote --tags origin "refs/tags/v$NEW"
git ls-remote --tags gitee  "refs/tags/v$NEW"
```

两行都必须返回 hash；任一失败即停止并报告，不要 force push。

## 10. 创建 GitHub Release

```bash
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  awk -v ver="$NEW" '
    $0 ~ "^## v" ver " " {p=1; next}
    p && /^## v/ {exit}
    p {print}
  ' CHANGELOG.md > /tmp/iris-release-notes.md
  gh release create "v$NEW" \
    --title "Iris v$NEW — <标题>" \
    --notes-file /tmp/iris-release-notes.md
else
  echo "gh 不可用或未登录；请手工创建：https://github.com/fengyoung/iris/releases/new?tag=v$NEW"
fi
```

若 Release 已存在，报告现有 URL，不覆盖。

## 11. 完成回执

必须输出以下信息，不能只说「已完成」：

| 项目 | 内容 |
|---|---|
| 版本 | `OLD → NEW`（bump 依据） |
| 提交 | 代码提交 hash + release 提交 hash |
| Tag | `vNEW`，origin / gitee 是否已推送 |
| Release | URL，或「未创建 + 手工链接」 |
| 门禁 | `TESTS` tests / `COVERAGE%` / Ruff / mypy / 安全扫描 / SBOM / pip-audit（CI 证据；依赖变更时 clean venv） |
| 未覆盖 | CI 3.11/3.12/3.13 矩阵与 macOS smoke（已推送，等待 CI） |

最后运行：

```bash
git status --short
git log --oneline -3
git describe --tags --abbrev=0
```

工作区应为干净（`coverage.json`、`dist/` 已忽略）。

## 12. 其它模式

### `commit` / `commit-push`

- 不推断版本、不改 5 个文件、不打 tag、不创建 Release。
- 流程：第 0~2 步 → `TYPE` 自选 → 第 4 步门禁（可省略 wheel/SBOM/audit，至少 Ruff + mypy + 全量 pytest）→ 第 5 步提交。
- `commit-push` 额外 `make push`。

### `merge <分支>`

```bash
git switch main
make pull
git merge --no-ff "<分支>" -m "merge: 合并 <分支>（<摘要>）"
```

冲突时停止并列出冲突文件，不自动解决。合并后跑 Ruff + mypy + 全量 pytest，
然后 `make push`；不自动发版。用户说「合并并发布」时再进入 release 流程。

### `dry-run`

只做第 0~1 步 + 第 3.2~3.3 的版本推断（**不执行第 2 步 `git add`**），输出：

- 当前版本 → 推断版本（bump 依据）
- 将要修改的 5 个文件
- 门禁计划
- 将要创建的 tag 与 Release 链接

不执行 `git add/commit/tag/push`。

## 13. 禁止事项

- 不 `git push --force`，不改写已发布历史，不删除远端分支。
- 不跳过失败的门禁，不伪造 Release 成功。
- 不把本地 conda/无关包的漏洞当成项目漏洞，也不因此跳过依赖变更时的重新审计。
- 不把 `.env`、`config/*.json`、`data/`、`dist/`、`build/` 加入版本控制。
- 不自动把 major 版本发出去（除非用户明确授权）。
- 不单独编辑 `AGENTS.md`。
- 不回溯补历史 tag。
