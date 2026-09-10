# Iris 发布检查清单

每次发布前必须在干净工作区完成以下检查，并将结果附在发布记录中。

## 质量与兼容性

- [ ] `ruff check src scripts tests`
- [ ] `mypy src/iris --no-incremental`
- [ ] `pytest -q`
- [ ] `pip wheel . --no-deps --no-build-isolation -w dist`
- [ ] Linux CI 与 macOS CLI smoke 均通过

## 安全与供应链

- [ ] `pip-audit` 成功连接漏洞源且无未豁免高危漏洞
- [ ] `python scripts/security_scan.py` 通过（无 eval/exec、pickle、shell=True 回归）
- [ ] 审查依赖升级、许可证与变更日志
- [ ] `python scripts/generate_sbom.py --output dist/iris.spdx.json` 生成并归档 SPDX 2.3 SBOM
- [ ] 检查 Git 历史和本次 diff 不含凭证、个人数据或业务正文
- [ ] 核验所有日志/异常输出不含 key、token、Authorization、会议或聊天正文

## 发布制品

- [ ] `iris --help` 可运行
- [ ] Docker 镜像以非 root 用户启动并能执行 `iris --help`
- [ ] 产品、协议和数据版本仅在对应层发生变化时递增
- [ ] 更新 CHANGELOG 与 SECURITY 支持策略
