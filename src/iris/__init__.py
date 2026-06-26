"""Iris — 工作知识助手。

版本体系（三层解耦）：
  __version__        协议版本（CLI 命令集 / agent-spec 格式）
  pyproject.toml     产品版本（SemVer，面向发布）
  config/*.json      数据版本（配置文件 Schema，独立演进）
"""

__version__ = "3.2"
