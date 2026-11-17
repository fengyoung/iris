# 外放参考回声衰减：实现说明与限制

日期：2026-11-16。状态：合成信号验证，未在实际 Mac 外放/耳机场景验证。已接入 transcription runtime 的 speaker 模式。

## 取舍

AVAudioEngine 的 voice processing 是输入/输出路由相关的系统能力，不能假定另一个会议进程输出已作为正确参考送入其回声处理链。为避免擅自改变会议音频路由或声称系统已消除回声，本次使用无设备依赖的 numpy 保守算法。

实际交付是有界远端参考、延迟搜索和单路径自适应幅度估计。它不是完整多抽头房间声学 AEC，不处理复杂混响，不保证识别双讲。后续如引入更完整 AEC，应保持旁路、原音和不确定归属规则。

## API

```python
from iris_interview.echo import ReferenceEchoCanceller

echo = ReferenceEchoCanceller()  # 16k mono, max delay 200ms, 2s reference history
# Both inputs must use the same session clock, after their resamplers.
echo.feed_remote(remote_samples, remote_start_ns)
result = echo.process_local(local_samples, local_start_ns)
# result.samples -> local VAD/ASR
# result.state / confidence / delay_samples -> diagnostic metadata
# result.attribution_uncertain is always True
```

`process_local` 返回新 float32 数组，长度不变，不删整段。没有对应参考（包含远端数据晚到）时立即原样旁路，不阻塞、不假造静音。调用方可在未来实现有界双路同步缓冲，但应明确报告超时旁路，不能无限等待远端。

输入块最多一秒；少于20ms旁路。历史内搜索0–200ms因果延迟，以去均值归一化相关性选择候选，仅相关性至少0.985、参考能量足够、增益合理时估计减法。高相关窗口更新增益；明显路径变化先旁路。低相关（包括大部分双讲）、缺参考、安静参考、异常增益或能量放大均旁路。

门控无法保证识别弱本地声音或恰好相关的双讲，因此保留 uncertain，不能把 attenuated 状态解释为干净单说话人。混响往往使相关性不足而旁路，这是保留真实声音的刻意选择。

## runtime 接入位置（已实现）

1. 只在 `speaker_mode == "speaker"` 创建 canceller；耳机不处理。
2. remote/native PCM downmix 并使用该轨 StreamingResampler 到16k后，按共同时间轴调用 `feed_remote`。remote本身继续原样送自己的Segmenter。
3. local重采样后、送Segmenter前调用 `process_local`，结果送local VAD/ASR。保留诊断状态，归属继续uncertain。
4. 需要精确记录每次重采样输出块起始sample位置：`track_origin_ns + produced_before * 1e9/16000`。不能简单以当前native packet时间替代累计样本时钟。
5. gap、pause/resume、格式变化、设备切换时reset。默认不额外持久化音频。
6. 开启录音时继续保存原始双路音频，不能只保存处理后的音频；manifest可以另加处理诊断。

## 证据

`tests/test_echo.py` 六项合成测试通过：延迟45ms、0.6倍回声的均方能量降低超过30dB；独立本地声音叠加时输出逐样本保持；缺参考/晚参考旁路；ring历史上限；不相关本地声音和静默不被移除；非法PCM和时戳拒绝。该结果仅证明受控合成路径，不能替代真实房间、扬声器、蓝牙、双讲及CPU负载验收。

另有 runtime 接入测试证明：speaker 模式在 VAD/ASR 前处理重采样输出、诊断延迟正确、实际送识别的回声能量下降，而 opt-in WAV 逐样本保留原始麦克风输入（仅16-bit量化误差）。两组共19项测试通过，Ruff通过。未录制真实音频，未读Keychain。
