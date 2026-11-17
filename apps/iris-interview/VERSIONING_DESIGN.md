# 知遇 · Iris-interview 版本管理设计 v1.0

日期：2026-11-16。
状态：设计提案；本文不表示发布工具、迁移机制或“关于”页面已实现，也不修改现有安装包。

## 1. 目标与参考

继承声笺的独立版本、单一版本来源、递增构建号、不可变发布、可验证归档和显式恢复机制，并覆盖知遇持久化面试 Project 的数据兼容风险。

参考现行实现：

- [声笺发布规则](../iris-caption/RELEASE.md)
- [声笺版本元数据](../iris-caption/src/iris_caption/version.py)
- [声笺数据兼容规则](../iris-caption/docs/data-compatibility.md)
- [知遇当前构建脚本](scripts/build_app.py)
- [知遇当前打包配置](IrisInterview.spec)

## 2. 当前状态与差距

知遇已生成多个互不覆盖的随机构建目录，但这些包均使用产品版本 0.1.0；最终交付的包是唯一应引用的旧版基线。

现状：

- 产品版本同时写在 pyproject.toml 与包 __init__.py；存在漂移风险。
- CFBundleVersion 与产品版本相同，没有独立递增 Build。
- 已有测试、冻结运行检查、签名和ZIP摘要验证，但构建与最终验证分离，没有统一的不可变发布事务。
- Project schema_version=1；导出包version=1；录音manifest version=1；音频IPC protocol=1。
- 岗位库写入schema_version=1，但需要补齐读取时版本拒绝与迁移合同；设置文件尚无schema_version。
- 岗位自身version、Project revision与评价确认快照已经存在，不能混为软件版本。
- 缺少统一关于页、发布记录、正式归档、迁移及回退工具。

## 3. 版本分层

| 层 | 形式 | 职责与规则 |
|---|---|---|
| 产品版本 | 0.1.1 / 0.2.0 / 1.0.0 | 用户看到的功能与修复版本，独立于主 Iris 和声笺 |
| 构建号 | 正整数，如 Build 17 | 每次真实构建尝试的唯一编号；严格递增，不表达兼容性 |
| 发布渠道 | development / preview / stable | 开发运行、开发预览、正式版；不因切换渠道允许覆盖同版本包 |
| 数据格式 | 独立整数schema | 数据库、Project、偏好、岗位库、导出包、录音manifest各自演进 |
| 业务配置 | 领导力标准1.0；岗位版本整数 | 评价口径与岗位内容，Project保存快照及版本 |
| 内容修订 | Project revision；计划/评价快照id | 并发保护与用户编辑历史，不随应用升级强制递增 |
| 组件协议 | 音频IPC整数；OCR协议整数 | 应用与内置helper兼容性握手；非产品版本 |
| 生成依据 | prompt版本、模型标识、来源修订 | 解释某次AI计划或评价如何产生，不承诺模型可完全复现 |

只有发生对应变化的层才升级。应用界面调整不升级Project schema；岗位内容更新不升级应用；模型回答变化不改领导力标准。版本号不作为识别数据是否兼容的唯一依据。

## 4. 产品版本与渠道规则

- PATCH：修复、稳定性、打包或诊断改进，不引入已知破坏性数据变化。
- MINOR：增加用户可见能力，或0.x阶段发生需明确说明的兼容变化。
- MAJOR：1.0之后的不兼容变化；仍须提供迁移/恢复说明，不能以大版本替代迁移责任。
- 0.x仍遵守数据保护，不以开发预览为理由覆盖或重置历史资料。
- stable至少要求目标设备上真实飞书/腾讯会议、主要耳机路径、长场次、备份恢复验收完成；外放等已知限制如实列出。
- 自动测试通过可交付preview，不自动将渠道提升为stable。
- 同一产品版本只发布一个正式受管制品集合。发布前失败可重试同版本但消耗新Build；成功发布后任何字节变化都必须递增产品版本，含仅修改渠道或签名。

建议下一次只引入版本管理的发布使用0.1.1 preview；若同时增加新产品功能则按实际范围选择0.2.0。本文不实际递增版本。

## 5. 单一来源与应用内显示

拟新增 src/iris_interview/version.py，字面量 __version__ 为唯一真相源。pyproject使用动态版本，__init__只重导出。构建脚本用AST读取版本，不为读版本导入应用或访问用户数据。

源码运行：Build 0，channel=development，构建日期为空；不能冒充发行包。

发行包嵌入 assets/build-info.json：

```json
{
  "version": "0.1.1",
  "build_number": 17,
  "built_at": "2026-11-16T10:00:00Z",
  "channel": "preview",
  "architecture": "arm64",
  "minimum_macos": "15.0",
  "source_revision": "sha256-prefix",
  "source_sha256": "full-source-fingerprint",
  "git_head": "context-only",
  "git_dirty": true
}
```

以上为字段示例，不是真实Build记录。

CFBundleShortVersionString=产品版本；CFBundleVersion=构建号的十进制字符串。打包spec必须验证暂存元数据版本和源码版本一致，不允许直接绕过正式发布入口生成无元数据发行包。

macOS菜单“关于知遇”和设置底部均提供入口，显示产品版本、Build、渠道、构建日期、架构、最低系统版本及源码标识；支持复制诊断摘要。复制内容不含密钥、Project正文、候选人姓名或本机私人路径。可展开查看数据/配置/协议版本及内置依赖版本。

## 6. 来源指纹与依赖追踪

指纹覆盖应用源码、测试、脚本、Swift源文件、构建配置、品牌资产、评价配置及实际被打包的声笺依赖源码。当前知遇直接复用声笺ASR模块，只记录知遇目录不足以标识真实制品。

构建清单额外记录：Python、PySide6、ONNX Runtime、funasr-onnx、Swift/SDK、PyInstaller版本，以及helper二进制SHA256、依赖包清单与关键来源摘要。模型权重不打包；运行时采用的模型摘要属于本场运行元数据，不伪称由软件版本固定。

构建前后校验来源指纹相同；变化则失败，不发布。git HEAD仅作上下文，不能代替未提交源码的真实指纹。

## 7. 发布流水线

拟提供统一 scripts/release.py；现有build/verify脚本作为内部步骤或迁移后废弃，避免两套可绕过的发行入口。

命令设计（尚未实现）：

```text
release.py build --dry-run
release.py build --channel preview
release.py verify --version 0.1.1
release.py import-legacy --dry-run
release.py import-legacy
release.py retention
release.py retention --apply
```

执行顺序：

1. dry-run只读取源码/环境并报告计划，不创建目录、不分配Build、不读用户数据。
2. 校验平台、版本未发布、构建环境依赖完整；不在发布过程中隐式升级依赖。
3. FileLock保护release-state，在锁内原子递增计数器并fsync；通过预检并预留的尝试即消耗Build，后续失败不回退编号。
4. 私有staging中生成build-info，构建helper、运行Ruff、全量测试与合成自测。
5. PyInstaller构建并检查冻结diagnostics、smoke、helper协议、图标、签名、未携带凭据/模型。
6. 通过ditto打包；验证ZIP、根目录、SHA256、大小，生成manifest与validation及文档快照。
7. 再次检查来源未变；以exclusive/no-replace原子发布完整目录。任何失败只保留失败日志，不产生“可交付”目录。
8. 显式报告最终版本、Build、路径、checksum、通过的检查与未验收事项。不自动安装、不删除旧包、不自动git提交或打tag推送。

release-state不能放在可清理dist中；损坏或丢失时拒绝从0重置，需核对所有发布/失败记录恢复计数器。单机构建为当前默认，多机必须共享锁语义可靠的状态与输出，或采用统一编号服务。

## 8. 制品布局与不可变性

```text
apps/iris-interview/
  releases/
    0.1.0/                       # 导入的既有最终预览包
    0.1.1/
      Iris-interview-0.1.1-macos-arm64.zip
      manifest.json
      validation.json
      CHANGELOG.md
      README.md
      QUICKSTART.md
      DATA_COMPATIBILITY.md
    archive/
  release-state/
    counter.json
    counter.lock
    attempts/                    # Build结果与失败摘要
  dist/                          # 可丢弃的中间构建目录
```

manifest包括产品/构建元数据、源指纹、依赖信息、ZIP摘要/大小、helper摘要、数据读写兼容矩阵和验证结论。validation区分通过、失败、未运行、需实机；不能将未运行写成通过。

禁止覆盖已发布版本，包括archive内同版本。保持当前与前一个版本在releases，其余经校验后移动到archive。retention默认预览、显式apply，仅归档不删除；未知目录、符号链接和不完整包不自动处理。应用数据备份与软件制品保留是两套独立策略。

同仓Git标签建议使用 iris-interview/v0.1.1，避免与声笺或主Iris冲突；仅在明确发布提交流程中创建，不能凭目录产物自动推送。

## 9. 既有0.1.0的纳管

只导入此前最终交付的包，按SHA256精确锁定：

```text
dist/build-8l9qvwdh/artifacts/Iris-interview-0.1.0-macos-arm64.zip
SHA256 0100328f73b8d1466b564b364dedf5915920fce17cfdd3f48b5c00e6e772179d
```

复制而不移动原包，保留旧verification。manifest声明legacy=true。原包没有新的Build元数据，build_number、可信构建日期和当时源指纹为null；导入时间另记imported_at，不能冒充构建时间。不得用当前源码推算并宣称旧二进制源指纹。

其他同标0.1.0的中间包不是发行版，不进入版本排序，也不自动删除。已纳管0.1.0后不得再重打包并覆盖它。

## 10. 数据格式与兼容管理

| 数据 | 当前状态 | 设计要求 |
|---|---|---|
| SQLite数据库结构 | 无独立数据库结构版本 | 新增user_version或迁移表，旧库视为0；与Project JSON版本独立 |
| Project JSON | schema_version=1 | 声明可读/可写版本；未知新版拒绝写入，不用默认数据覆盖 |
| 设置/偏好 | 未版本化JSON | 纳管为schema1，无标记旧文件视为0，保留未知扩展字段 |
| 岗位库 | schema_version=1 | 严格读取校验；岗位内容version独立递增 |
| 导出ZIP包 | format+version=1 | 分别校验外包与Project格式，原ZIP不可修改；导入到新id |
| 录音manifest | version=1 | 保留时钟语义及文件映射；不改写音频波形，未知版本拒绝回听映射 |
| 音频IPC | protocol=1 | 握手版本不匹配明确失败，不错误解析PCM |
| OCR输出 | 目前无显式协议版本 | 后续加协议标记，旧响应兼容需测试；helper随app固定发布 |
| 领导力标准 | version=1.0 | 版本+内容hash；旧Project快照不被全局更新覆盖 |
| Keychain/模型/原件 | 外部或不可变资产 | 不参与默认JSON迁移，不删除或复制密钥，不改动原件/模型 |

每个发行版声明读取版本范围、写入版本、迁移起点。损坏数据/未知版本明确报错并保留原件；只有已验证解析器可提供只读打开，不能为了“能打开”猜测新结构。

迁移必须有原子性与完整备份：

- 关闭采集与后台写任务，获取跨进程维护锁；所有写入口遵守同一维护锁。
- SQLite使用backup API得到一致快照，不能仅复制可能依赖WAL的主数据库文件。
- 若涉及多文件Project状态，生成包含数据库、附件索引和所需文件的完整generation；校验后原子切换激活指针，或提供有恢复日志的等价事务。不能宣称简单JSON原子写可保证多文件事务。
- 先备份并校验，再变更；备份失败则阻止迁移。迁移失败保持可打开的旧generation及完整错误信息，不重置数据。
- 首个写入新版数据前记录迁移源/目标schema、工具版本、时间、内容摘要与备份定位。不存候选人正文到诊断日志。
- 幂等迁移；重试不重复添加字段或破坏附件关系。旧版本扩展字段在受支持当前格式内保留，不能把它误当作支持未知新版。

## 11. 升级与回退

升级前：停用采集与后台任务，检查兼容矩阵；需要schema迁移时自动创建并验证迁移备份。版本更新不主动重新生成面试计划、转写校正或评价。

回退有两种情况：

1. 数据仍在旧应用支持范围：退出所有实例，保留当前数据备份后运行旧app。
2. 新数据旧应用不支持：同时恢复匹配的旧应用与迁移前完整数据快照；先保留升级后数据，不修改schema标记伪装旧格式。

恢复旧快照会失去该快照之后的修改，必须展示时间与影响。不能默认用旧版打开新库来自动降级。优先恢复到独立目录核验，再显式切换数据根。回退默认不触碰钥匙串或本地模型。

## 12. 评价与生成结果溯源

历史计划/评价记录：创建应用版本及Build、岗位id/版本与快照摘要、领导力配置版本/摘要、prompt版本、模型服务标识与model、生成时间、来源Project revision、引用转写id及片段revision。

记录provider时去除URL凭据/查询参数；绝不记录API密钥。云端model名称不是权重版本，报告注明其重现性限制。

修改标准后，历史结论仍按旧快照解释。用户明确选择新标准重新评价时创建新草稿与确认版本，不覆盖旧正式评价。

## 13. 验收与实施顺序

### 第一批：发布身份与制品

单一version.py、动态pyproject、build-info、关于页、Build计数器、源指纹、不可变release入口、legacy导入、CHANGELOG与retention。

验收：并发编号不重复、失败消耗编号、dry-run无副作用、已有版本拒绝覆盖、构建中源码改变阻止发布、包内/manifest/关于页元数据一致、冻结启动不依赖开发目录。

### 第二批：数据兼容与回退

偏好/岗位库/数据库版本门禁、迁移备份、SQLite一致快照、恢复到独立目录、版本读写矩阵。

验收：旧数据升级、未知新版拒绝、坏JSON/WAL/附件缺失、迁移中断/磁盘写失败、两进程竞争、恢复后原文/转写/录音映射/评价历史一致。仅使用临时合成数据，不对真实用户目录试迁移。

### 第三批：生成依据与反馈

生成元数据、标准快照hash、引用revision、诊断复制及版本化配置展示。

验收：人工职级不受AI或迁移影响、旧评价不被配置更新改写、反馈无秘密和候选人信息、正式版本的来源可追溯。

这三批全部落地后，再将版本管理描述为已实现；本设计阶段只新增文档，不改产品版本、安装包、release-state或用户数据。
