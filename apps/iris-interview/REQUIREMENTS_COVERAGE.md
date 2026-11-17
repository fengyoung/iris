# 知遇开发交付范围映射

| 需求 | 实现模块 | 验证 |
|---|---|---|
| 本地Project与历史 | storage / ui / search_ui / notes_ui | 并发revision、搜索、归档、回收站、迁移、快照测试 |
| 岗位库与v1标准 | catalog / job_ui / config | 表单与模型草稿校验，版本快照 |
| 简历原文重排 | resume / ocr / material_ui | 文本保留、混合PDF、本地Vision、原件预览 |
| 问题对齐 | aligned_resume / draft_ui | 共同行进、人工状态连续保存、主题 |
| 定制问题与局部改写 | services / ui | block引用、id/status保留、非法输出拒绝 |
| 双轨音频 | native / transcription | Swift编译、PCM协议、pause/gap/stop、两轨来源 |
| 实时音量与初稿 | transcription / capture_ui / ui | ASR队列前meter、partial/final事件 |
| 外放回声 | echo / transcription | 合成高相关衰减、双讲旁路，真实效果待验 |
| 可选录音与回听 | transcription / storage / playback | opt-in、manifest时间映射、恢复、备份 |
| 校正与人工优先 | services / ui | 输出边界、revision/人工锁、失败重试入口 |
| 面后评价与人工职级 | services / evaluation_ui | 六维、证据合法、人工可编辑、无AI职级 |
| 历史正式评价 | storage / notes_ui.HistoryDialog / export | 快照、复核标记、最新确认版导出 |
| 深浅主题与品牌 | theme / assets | 离屏两主题、反色默认icon、打包hash |
| 独立应用交付 | scripts / IrisInterview.spec | build、冻结smoke、双helper、签名检查、ZIP |

## 人工设备验收仍需开展

真实会议软件权限与采集、蓝牙设备切换、外放场景及长场次性能未进行现场验收。所有自动测试和合成语音结果均明确其边界，不把开发预览称为生产验收版。

## 不属于本版的扩展

多人会议的远端身份自动识别、云同步、跨轮综合评分、招聘平台自动提交、Apple公证与公开分发均不在本次开发范围。
