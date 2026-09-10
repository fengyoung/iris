# Iris v3.35.0 三阶段工程优化记录（2026-09-10）

## 阶段一：输入边界与敏感信息

- 飞书远程图片仅允许 HTTPS 公网地址，禁止本机、内网、保留地址和自动重定向。
- 下载增加 Content-Length、响应体大小和 Content-Type 校验，写入使用原子写与写入守卫。
- SOURCE 同级 `Pic/` 和显式图片目录纳入写入白名单。
- 任务面板不再默认保存完整 `sys.argv`，命令摘要会截断并脱敏敏感参数。
- 任务面板响应增加 `nosniff`、`DENY`、`no-referrer` 等安全头。
- macOS Keychain 写入增加 Security.framework 原生路径，避免密钥进入子进程参数。

## 阶段二：质量、类型与并发

- `ProcessRegistry` 使用独立锁文件串行化注册，PID 文件使用 0600 权限并避免旧进程误删新 owner 文件。
- `FileLock` 锁文件使用 0600，并在写入 PID 前截断文件，避免残留内容污染诊断。
- 配置加载不再用宽泛异常捕获，区分 Pydantic 校验错误和输入错误。
- 开启 mypy `check_untyped_defs`，修复存量 6 个类型错误。
- 新增关键模块覆盖率门禁脚本，避免总覆盖率掩盖核心路径回归。
- 新增异步 HTTP、远程下载安全、任务摘要和进程注册回归测试。

## 阶段三：供应链与自动化门禁

- 新增标准库实现的 SPDX 2.3 SBOM 生成脚本，并纳入 CI artifact。
- 新增 AST 安全扫描，阻断 `eval`、`exec`、`pickle` 和 `shell=True` 回归。
- CI 纳入 SBOM、关键模块覆盖率和安全静态扫描步骤。
- 补充 ASR/转写可选依赖约束，减少 extra 安装结果漂移。

## 发布验证与 CI 约定

- `pytest -q` 全量 3,326 项通过；Ruff、严格 mypy、AST 安全扫描和 SPDX SBOM 生成通过。
- CI 先运行 unit、再运行 integration，并以同一覆盖率数据合并后统一执行全局 `fail_under=65` 与关键模块门禁；unit 子集执行时显式关闭覆盖率阈值，避免其覆盖率尚未合并便提前失败。
- 任务面板 `ps` 探测与会议助理 FunASR 均为系统/可选依赖边界。相关单测使用 mock 验证本模块逻辑，不触发真实进程探测、模型加载或网络下载。

## 后续治理项

以下事项已纳入下一迭代，不在本次变更中进行高风险大规模重构：

1. 将 `analysis/service.py`、`wiki/generator.py`、`assistant/live.py` 等大型编排模块拆成 application service + adapter。
2. 将任务历史迁移到 SQLite 或按日期分片，避免 JSONL 长期增长。
3. 为任务面板增加可选访问令牌，并增加数据保留/删除策略。
4. 继续收窄外部服务调用处的 `except Exception`，逐版减少 `mypy ignore_errors` 模块。
