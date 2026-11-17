# 知遇独立应用开发约定

本目录为独立 macOS 应用，不导入主 Iris 运行时。产品版本独立，不递增主仓版本。

- Python 3.11+ / PySide6；原生采集桥接位于 native，macOS 15+。
- 自定义异常继承本应用 exceptions.InterviewError；作为独立发行包不依赖 iris.core。文件持久化使用 FileLock 与原子写入，SQLite 显式关闭。
- 源码不得包含候选人真实数据、凭据或个人岗位库；测试使用合成材料。
- ASR 依赖可复用 iris-caption 包的无 UI 模块，不修改声笺现有代码；打包须包括依赖。
- 长任务用应用进度、取消和错误事件反馈；不依赖 Iris TaskReporter 服务。
- AI 生成不得覆盖人工编辑；suggested_level 永远仅供人工填写。
- 所有 UI 错误可见；无配置、无模型、无权限不得假装成功或默认生成伪评价。
- 本目录代码由主代理集成；native 由 audio 代理独立修改，storage.py/test_storage.py 由 storage 代理独立修改。
