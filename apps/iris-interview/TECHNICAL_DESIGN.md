# 知遇技术设计与验证计划

## 架构决定

- 独立 Python/PySide6 桌面应用，产品版本 0.1.0；不修改声笺、不升级主 Iris。
- 原生 Swift ScreenCaptureKit 子进程负责双路采集，macOS 15+。NDJSON v1 传递事件和有界 PCM 块；local/remote 独立，时间戳共同。只在用户点击开始时采集。
- Python 消费音频，按轨道维护重采样/VAD/分段，后台串行调用本地 ASR，避免并行模型超内存。复用声笺独立包无 UI ASR 模块。云端校正独立队列，不阻止本地识别。
- SQLite 事务保存 JSON Project 和检索数据；FileLock 串行附件及导入导出，revision 检测旧窗口覆盖。原文件副本、录音和历史版本分项目管理，完整 ZIP 支持迁移。
- GUI 线程只处理界面与短事务，网络/解析/模型/导出放工作线程。任务进度、取消与错误展示由应用自身负责。
- 模型使用 OpenAI-compatible HTTPS chat completions，endpoint/model 可配置，钥匙串保管密钥。初版不依赖主 Iris 服务。
- 模型输出是受验证的 JSON：问题必须关联已知 block id；评价只能引用已有 transcript id；建议职级不在模型输出合同内。
- 项目与评价快照支持源 revision/人工编辑保护，迟到结果展示为待采用建议。

## 核心数据合同

ProjectStore 返回 schema_version/id/revision/时间戳、候选人/岗位/轮次/状态、resume、job_snapshot、plan、transcript、notes、evaluation、attachments、snapshots。

resume.blocks: [{id,text,page?}]，正文文本仅用户校对修改。plan.questions: [{id,block_id,text,purpose,followups,dimensions,priority,status}]。
transcript: [{id,source,speaker,start,end,raw,corrected,manual,revision,status}]。
notes: [{id,text,time,question_id?,kind}]。
evaluation.draft 保存领导力/专项技能/结果及证据引用；evaluation.suggested_level 仅人工字段。confirmed/snapshots 存正式历史。

## 音频真实性边界

可自动验证：helper编译、自测协议、虚拟PCM的时序/分轨/暂停/异常、无音频落盘默认、模型本地样本。

需实机确认：飞书/腾讯会议授权后的真实双方收音、蓝牙链路、外放回声、四十分钟以上持续运行。不会在开发过程中自动开启麦克风或录制用户会话。外放若无可靠AEC不能以简单静音或文本去重冒充解决；必须明确限制与未验证项。

## 验证顺序

1. Swift build/self-test 与存储并发、恢复、安全导入。
2. 无真实数据的Qt离屏工作台、PDF/文字解析、主题和问题对齐。
3. 模型输出验证、生成冲突、评价确认和证据跳转。
4. 音频桥接/ASR集成，应用诊断与演示。
5. 打包独立app及依赖检查，书面真实设备验收清单。
