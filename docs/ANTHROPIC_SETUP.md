# Anthropic 模型配置指南

本文档说明如何在 Iris 3 中配置和使用 Anthropic 的 Claude 模型（包括多模态支持）。

## 功能支持

✅ **已支持**：
- Anthropic Messages API（纯文本）
- Anthropic 多模态 API（文本 + 图片）
- 自动降级链（与 OpenAI 兼容模型混合使用）
- Token 用量统计
- 响应缓存
- 熔断器保护

## 配置步骤

### 1. 设置环境变量

在 `.env` 文件中添加 Anthropic API 凭证：

```bash
# Anthropic API 配置
IRIS_ANTHROPIC_BASE_URL=https://api.anthropic.com
IRIS_ANTHROPIC_API_KEY=sk-ant-api03-xxxxx...
IRIS_ANTHROPIC_PROVIDER=anthropic  # 可选，默认值就是 anthropic
```

**通道命名规则**：环境变量前缀 `IRIS_<CHANNEL>_*` 中的 `<CHANNEL>` 必须与配置文件中的 `channel` 字段匹配（大写并将 `-` 替换为 `_`）。

### 2. 在 llm.json 中添加模型配置

编辑 `config/llm.json`，在相应的模型角色下添加 Anthropic 模型：

```json
{
  "models": {
    "adv_model": {
      "enabled": true,
      "default_model_id": "claude-sonnet-4",
      "models": {
        "claude-sonnet-4": {
          "channel": "anthropic",
          "model": "claude-sonnet-4-20250514",
          "display_name": "Claude Sonnet 4",
          "multimodal": true,
          "max_context_tokens": 200000,
          "max_tokens": 8192,
          "temperature": 0.2,
          "top_p": 1.0,
          "timeout_seconds": 120,
          "max_retries": 2,
          "priority": 110,
          "cost_level": "high",
          "reasoning_level": "advanced",
          "supported_inputs": ["text", "image"],
          "use_cases": [
            "qa",
            "wiki_extract_multimodal",
            "analysis_complex",
            "image_understanding"
          ],
          "notes": "Claude Sonnet 4 官方 API"
        },
        "claude-opus-4": {
          "channel": "anthropic",
          "model": "claude-opus-4-20250514",
          "display_name": "Claude Opus 4",
          "multimodal": true,
          "max_context_tokens": 200000,
          "max_tokens": 8192,
          "temperature": 0.2,
          "top_p": 1.0,
          "timeout_seconds": 180,
          "max_retries": 2,
          "priority": 105,
          "cost_level": "very_high",
          "reasoning_level": "advanced",
          "supported_inputs": ["text", "image"],
          "use_cases": [
            "qa",
            "wiki_rebuild",
            "analysis_complex",
            "conflict_resolution"
          ],
          "notes": "Claude Opus 4，最强推理能力"
        },
        "claude-haiku-4": {
          "channel": "anthropic",
          "model": "claude-haiku-4-20250514",
          "display_name": "Claude Haiku 4",
          "multimodal": true,
          "max_context_tokens": 200000,
          "max_tokens": 8192,
          "temperature": 0.2,
          "top_p": 1.0,
          "timeout_seconds": 60,
          "max_retries": 2,
          "priority": 95,
          "cost_level": "low",
          "reasoning_level": "standard",
          "supported_inputs": ["text", "image"],
          "use_cases": ["qa", "summary"],
          "notes": "Claude Haiku 4，快速响应"
        }
      }
    }
  }
}
```

### 3. 配置字段说明

| 字段 | 必填 | 说明 |
|------|:----:|------|
| `channel` | ✅ | 必须为 `"anthropic"`，用于关联环境变量 |
| `model` | ✅ | Anthropic API 的模型标识符 |
| `multimodal` | ✅ | 是否支持多模态（Claude 4 系列都支持） |
| `max_tokens` | ✅ | 最大输出 token 数 |
| `timeout_seconds` | ✅ | HTTP 请求超时时间（秒） |
| `priority` | ✅ | 降级链优先级（数值越大优先级越高） |
| `temperature` | 可选 | 默认温度参数（0-1） |
| `max_retries` | 可选 | 失败重试次数 |

## 使用示例

### 纯文本调用

```python
from iris.llm import LLMService
from iris.config.loader import load_config

config = load_config()
llm_service = LLMService(config)

# 使用路由自动选择模型
result = llm_service.generate(
    prompt="解释量子计算的基本原理",
    route_context={"task_type": "qa"},
)
print(result.text)
print(f"使用模型: {result.model}")

# 强制使用特定 Anthropic 模型
result = llm_service.generate(
    prompt="解释量子计算的基本原理",
    force_model="claude-sonnet-4-20250514",
)
```

### 多模态调用

```python
import base64

# 读取图片并转换为 base64
with open("diagram.png", "rb") as f:
    image_data = base64.b64encode(f.read()).decode("utf-8")

# 构造多模态内容
content_parts = [
    {"type": "text", "text": "请分析这张架构图"},
    {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{image_data}"}
    }
]

# 调用多模态 API
text = llm_service.generate_multimodal(
    content_parts=content_parts,
    route_context={"input_type": "multimodal", "task_type": "image_understanding"},
)
print(text)
```

### CLI 命令

```bash
# 使用 Anthropic 模型进行问答
iris ask "量子计算的基本原理是什么？" --model claude-sonnet-4-20250514

# 处理图片（会自动路由到多模态模型）
iris process-complex-input image.png --prompt "描述这张图片"

# 查看当前模型配置
iris llm-info

# 切换活跃模型
iris switch-model adv_model claude-opus-4
```

## 多模态格式说明

LLMService 内部会自动转换格式：

**输入格式**（OpenAI 兼容）：
```python
content_parts = [
    {"type": "text", "text": "描述这张图片"},
    {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ..."}
    }
]
```

**转换为 Anthropic 格式**（自动完成）：
```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "描述这张图片"},
        {
          "type": "image",
          "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": "/9j/4AAQ..."
          }
        }
      ]
    }
  ]
}
```

**限制**：
- ✅ 支持 `data:` URI 格式的 base64 编码图片
- ❌ 暂不支持直接传递 HTTP URL（Anthropic API 限制）

## 降级链配置

Anthropic 模型可以与 OpenAI 兼容模型混合使用，通过 `priority` 字段控制降级顺序：

```json
{
  "models": {
    "adv_model": {
      "models": {
        "claude-sonnet-4": {"priority": 110},          // 首选
        "qwen3.8-max-zz": {"priority": 100},           // 降级备选 1
        "deepseek-v4-flash-vision-exp": {"priority": 95} // 降级备选 2
      }
    }
  }
}
```

当 Claude Sonnet 4 失败时，会自动尝试 Qwen 3.8 Max，依次类推。

## 常见问题

### Q: 如何验证配置是否正确？

```bash
# 检查环境变量
env | grep IRIS_ANTHROPIC

# 测试连接
iris ask "Hello" --model claude-sonnet-4-20250514
```

### Q: 多模态调用失败怎么办？

1. 确认模型配置中 `multimodal: true`
2. 确认图片是 base64 编码的 data URI
3. 检查图片大小（建议 < 5MB）
4. 查看日志：`tail -f data/logs/iris.log`

### Q: Token 用量如何统计？

```bash
# 查看用量统计
iris llm-usage --provider anthropic

# 查看详细用量
iris llm-usage --detailed
```

### Q: 如何优化成本？

1. 使用 Haiku 4 处理简单任务（`cost_level: "low"`）
2. 只在复杂任务使用 Sonnet/Opus（`cost_level: "high"`）
3. 配置合理的路由规则（`config/llm.json` 的 `routing.rules`）
4. 启用响应缓存（`temperature=0` 时自动启用）

## 版本兼容性

- Iris 版本：>= 3.34.2
- Anthropic API 版本：2023-06-01
- 支持的 Claude 模型：Claude 3/4 全系列

## 技术实现

相关代码文件：
- `src/iris/llm/provider.py`: 核心实现（`_call_anthropic_multimodal`）
- `src/iris/llm/service.py`: 服务层封装
- `src/iris/llm/router.py`: 模型路由
- `tests/test_anthropic_multimodal.py`: 单元测试
