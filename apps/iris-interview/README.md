# 知遇 · Iris-interview

个人使用的 macOS 面试工作台，版本 0.1.2 开发预览。更新说明见 [CHANGELOG.md](CHANGELOG.md)。

## 环境

macOS 15+（原生音频桥接要求），Apple Silicon 优先验证；Python 3.11+，PySide6。独立应用，不依赖主 Iris 服务。

开发环境可使用仓库内声笺已有虚拟环境，避免修改其依赖：

```bash
cd apps/iris-interview
PYTHONPATH=src:../iris-caption/src ../iris-caption/.venv/bin/python -m iris_interview.app --demo
```

独立开发环境需要安装本目录与相邻 `iris-caption` 的包及 ASR 可选依赖。示例：

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e '.[asr,dev]' -e ../iris-caption
sh native/build.sh
sh native-ocr/build.sh
.venv/bin/python -m iris_interview.app
```

`--demo` 使用临时目录和合成简历，不调用云端、不访问钥匙串、不开始录音。`--data-dir PATH` 显式指定工作数据目录。常规数据在 `~/Library/Application Support/Iris-interview`。

## 使用

1. 设置浅色或深色主题、模型服务地址与模型名称；API 密钥保存在 macOS 钥匙串。
2. 导入或创建岗位；新建 Project，导入简历并校对文字。PDF 的扫描页与图片可本地 Vision OCR，始终需要对照原件确认。
3. 选择本场岗位快照并生成问题草稿，确认采用后进入简历与问题对齐工作台；可直接人工添加问题。
4. 在飞书/腾讯会议中正常通话，知遇的收音检查页显式授权、选择应用和麦克风后开始。建议耳机。默认不保存录音。
5. 结束后整理文字、笔记，主动生成评价。评级和结论可修改；建议职级仅人工可选填写。
6. 确认评价形成正式版本；导出默认使用最新正式版本。首页可检索、归档、移入回收站和恢复。

## 模型

语音识别复用声笺 contextual Paraformer ONNX + FSMN 本地模型适配器。设置中指定完整模型目录；不自动下载或导出模型。模型缺失时明确报错，不退化为假转写。云端是 OpenAI-compatible chat completions 接口，HTTP仅允许本机地址，其余HTTPS。

云端处理材料属于明确启用的文本任务。生成内容必须由用户审阅；无模型配置仍可人工整理简历、问题和评价。

## 验证与限制

```bash
PYTHONPATH=src:../iris-caption/src QT_QPA_PLATFORM=offscreen ../iris-caption/.venv/bin/python -m iris_interview.app --smoke-test
../iris-caption/.venv/bin/python -m pytest -q
python3 native/test_protocol.py
```

原生 helper 的 `--diagnostics` 不请求权限、不采音；`--request-permission` 仅在用户操作后执行。编译、合成音频协议测试、离屏UI通过不代表真实会议验收通过。

当前外放提供保守参考回声衰减，高相关时减弱匹配回声，双讲或缺参考时保留原声；不能宣称实现可靠房间回声消除。外放归属标为待核实，不以删除重叠发言冒充去回声。真实飞书/腾讯会议、蓝牙设备切换、长场次性能与外放效果需实机验收。应用尚未 Apple 公证。

完整范围见仓库 `docs/iris-interview-requirements-v1.0.md`；技术边界见 `TECHNICAL_DESIGN.md`。
