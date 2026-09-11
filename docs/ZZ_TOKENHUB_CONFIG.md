# zz_tokenhub 双协议配置说明

## 📋 配置概述

`zz_tokenhub` 通道同时支持两种 LLM 协议，通过不同的 Base URL 访问：

| 协议 | Base URL | 用途 |
|------|----------|------|
| **OpenAI 兼容** | `https://tokenhub.zhuanspirit.com/codex/v1` | DeepSeek、Qwen、GPT 等模型 |
| **Anthropic** | `https://tokenhub.zhuanspirit.com/anthropic` | Claude 系列模型 |

## ✅ 当前配置状态

### BASE_MODEL (4 个模型)

| 模型ID | 协议 | Base URL | 优先级 | 状态 |
|--------|------|----------|:---:|:----:|
| `claude-sonnet-5-zz` ⭐ | Anthropic | `/anthropic` | 110 | ✅ |
| `qwen3.8-flash-zz` | OpenAI | `/codex/v1` | 105 | ✅ |
| `deepseek-flash-zz` | OpenAI | `/codex/v1` | 100 | ✅ |
| `deepseek-flash` | OpenAI | 官方 DeepSeek | 95 | ✅ |

### ADV_MODEL (7 个模型)

| 模型ID | 协议 | Base URL | 优先级 | 状态 |
|--------|------|----------|:---:|:----:|
| `claude-fable-5-zz` ⭐ | Anthropic | `/anthropic` | 120 | ✅ |
| `qwen3.8-max-zz` | OpenAI | `/codex/v1` | 110 | ✅ |
| `qwen3.7-plus-zz` | OpenAI | `/codex/v1` | 75 | ✅ |
| `qwen3.6-plus-zz` | OpenAI | `/codex/v1` | 70 | ✅ |
| `qwen3.8-flash-bl` | OpenAI | 百炼 | 55 | ✅ |
| `qwen3.7-plus-bl` | OpenAI | 百炼 | 50 | ✅ |
| `gpt-5.6-sol-zz` | OpenAI | `/codex/v1` | 35 | ⚠️ 末位兜底 |

⭐ = 默认模型 · 优先级降序即降级链顺序 · ⚠️ `gpt-5.6-sol-zz` 高能力但实测连接易超时，仅作末位兜底，勿设为默认

## 🔧 环境变量配置

只需要配置 `zz_tokenhub` 的 API Key，Base URL 已在模型配置中指定：

```bash
# .env 文件

# zz_tokenhub API Key (必需)
IRIS_ZZ_TOKENHUB_API_KEY=your_api_key_here

# 以下可选（已在模型配置中指定）
# IRIS_ZZ_TOKENHUB_BASE_URL=https://tokenhub.zhuanspirit.com/codex/v1
# IRIS_ZZ_TOKENHUB_PROVIDER=openai

# DeepSeek 官方（可选）
IRIS_DEEPSEEK_BASE_URL=https://api.deepseek.com
IRIS_DEEPSEEK_API_KEY=your_deepseek_key

# 百炼（可选）
IRIS_BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
IRIS_BAILIAN_API_KEY=your_bailian_key
```

## 📊 配置详情

### Claude 模型（Anthropic 协议）

```json
{
  "channel": "zz_tokenhub",
  "provider": "anthropic",
  "api_base_url": "https://tokenhub.zhuanspirit.com/anthropic",
  "model": "claude-sonnet-5" // 或 "claude-fable-5"
}
```

### 其他模型（OpenAI 兼容协议）

```json
{
  "channel": "zz_tokenhub",
  "provider": "openai",
  "api_base_url": "https://tokenhub.zhuanspirit.com/codex/v1",
  "model": "deepseek-flash" // 或其他 OpenAI 兼容模型
}
```

## 🎯 工作原理

1. **配置解析**：`config/loader.py` 的 `resolve_channels()` 函数处理模型配置
2. **字段优先级**：模型配置中的 `api_base_url` 和 `provider` 会覆盖通道默认值
3. **协议分发**：`provider.py` 的 `_dispatch_provider_call()` 根据 `provider` 字段分发到对应协议实现
4. **自动降级**：失败时按 `priority` 降序尝试其他模型

## ✨ 优势

1. **灵活配置**：同一通道支持多种协议
2. **透明切换**：上层代码无感知，自动路由到正确的协议
3. **统一认证**：只需一个 API Key（`IRIS_ZZ_TOKENHUB_API_KEY`）
4. **智能降级**：Claude 失败可降级到 DeepSeek/Qwen 等模型

## 🧪 验证配置

运行验证脚本：

```bash
python << 'EOF'
import json
from pathlib import Path

config = json.loads(Path("config/llm.json").read_text())

print("验证 zz_tokenhub 配置：\n")
for role, cfg in config["models"].items():
    for mid, mcfg in cfg["models"].items():
        if mcfg.get("channel") == "zz_tokenhub":
            provider = mcfg.get("provider")
            url = mcfg.get("api_base_url")
            print(f"✅ {mid:<30} {provider:<10} {url}")
EOF
```

## 📝 配置文件位置

- **模型配置**: `config/llm.json`
- **环境变量**: `.env`（gitignored）
- **示例配置**: `.env.example`

## 🔄 更新日期

2026-09-11 - 模型矩阵同步至 base 4 / adv 7，全矩阵多模态；新增优先级列与降级链说明
2026-09-09 - 初始配置完成
