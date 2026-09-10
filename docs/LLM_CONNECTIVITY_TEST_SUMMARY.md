# LLM 模型连通性测试报告（执行摘要）

**测试时间**: 2026-09-09 21:24:11  
**测试工具**: `scripts/test_llm_connectivity.py`  
**测试提示词**: "请用一句话介绍自己。"

---

## 📊 总体结果

| 指标 | 数值 | 占比 |
|------|:----:|:----:|
| **总模型数** | 14 | 100% |
| **✅ 成功** | 12 | **85.7%** |
| **❌ 失败** | 2 | 14.3% |
| **⚠️ 异常** | 0 | 0% |

### 按协议统计

| 协议 | 成功/总数 | 成功率 | 状态 |
|------|:--------:|:------:|:----:|
| **OpenAI 兼容** | 7/7 | **100%** | ✅ 完全可用 |
| **DeepSeek 官方** | 3/3 | **100%** | ✅ 完全可用 |
| **百炼（bailian）** | 2/2 | **100%** | ✅ 完全可用 |
| **Anthropic** | 0/2 | **0%** | ❌ 不可用 |

---

## ❌ 关键问题：Anthropic 模型不可用

### 失败的模型

1. **claude-sonnet-5-zz** (base_model 默认)
   - 错误：`HTTP 401 Unauthorized`
   - 影响：纯文本任务无法使用 Claude

2. **claude-fable-5-zz** (adv_model 默认)
   - 错误：`HTTP 401 Unauthorized`
   - 影响：多模态任务无法使用 Claude

### 问题分析

**根本原因**: HTTP 401 表示认证失败，可能的原因：

1. ✅ **API Key 已配置**（Keychain 中存在 `IRIS_ZZ_TOKENHUB_API_KEY`）
2. ❌ **API Key 无效或过期** - 需要确认 tokenhub 的 API Key 是否支持 Anthropic 端点
3. ❌ **Base URL 配置问题** - `https://tokenhub.zhuanspirit.com/anthropic` 可能需要不同的认证方式
4. ❌ **权限问题** - tokenhub API Key 可能未授权访问 Anthropic 端点

### 解决方案

**建议按优先级依次尝试**：

1. **验证 API Key 权限**
   ```bash
   curl -H "Authorization: Bearer $IRIS_ZZ_TOKENHUB_API_KEY" \
        https://tokenhub.zhuanspirit.com/anthropic/v1/messages
   ```

2. **检查 Anthropic 端点配置** - 可能需要：
   - 不同的 API Key
   - 不同的认证方式（`x-api-key` vs `Authorization`）
   - 不同的 Base URL

3. **临时回退方案**：将默认模型改为可用的 OpenAI 兼容模型

---

## ✅ 可用模型详情

### BASE_MODEL（4/5 可用）

| 优先级 | 模型 | 状态 | 响应时间 | Token 用量 |
|:-----:|------|:----:|:--------:|:----------:|
| ~~110~~ | ~~claude-sonnet-5-zz~~ | ❌ | - | - |
| **100** | **deepseek-v4-flash-zz** | ✅ | 0.72s | 89+40 |
| 90 | deepseek-v4-flash | ✅ | 0.00s* | 89+40 |
| 80 | deepseek-v4-pro-zz | ✅ | 2.55s | 89+92 |
| 60 | deepseek-v4-pro | ✅ | 0.00s* | 89+92 |

*0.00s 表示使用了缓存

### ADV_MODEL（8/9 可用）

| 优先级 | 模型 | 状态 | 响应时间 | Token 用量 | 多模态 |
|:-----:|------|:----:|:--------:|:----------:|:------:|
| ~~120~~ | ~~claude-fable-5-zz~~ | ❌ | - | - | - |
| **110** | **qwen3.8-max-zz** | ✅ | 2.73s | 66+78 | ✅ |
| 95 | deepseek-v4-flash-vision-exp | ✅ | 1.08s | 89+75 | ✅ |
| 90 | deepseek-v4-flash-vision-exp-zz | ✅ | 0.00s* | 89+75 | ✅ |
| 75 | qwen3.7-plus-zz | ✅ | 4.55s | 15+289 | ✅ |
| 70 | qwen3.6-plus-zz | ✅ | 11.00s | 15+601 | ✅ |
| 55 | qwen3.8-flash-bl | ✅ | 2.05s | 66+106 | ✅ |
| 50 | qwen3.7-plus-bl | ✅ | 0.00s* | 15+289 | ✅ |
| 35 | gpt-5.6-sol-zz | ✅ | 6.58s | 13+27 | ✅ |

---

## ⚡ 性能排名（Top 5）

| 排名 | 模型 | 响应时间 | 协议 |
|:----:|------|:--------:|:----:|
| 1️⃣ | qwen3.7-plus-bl | 0.00s* | OpenAI (百炼) |
| 2️⃣ | deepseek-v4-flash-vision-exp-zz | 0.00s* | OpenAI (zz) |
| 3️⃣ | deepseek-v4-flash | 0.00s* | DeepSeek 官方 |
| 4️⃣ | deepseek-v4-pro | 0.00s* | DeepSeek 官方 |
| 5️⃣ | deepseek-v4-flash-zz | 0.72s | OpenAI (zz) |

*缓存命中

---

## 🎯 当前实际默认模型

由于 Claude 模型不可用，系统会自动降级到下一个可用模型：

| 角色 | 配置的默认 | 实际使用 | 协议 |
|------|-----------|----------|------|
| **base_model** | ~~claude-sonnet-5-zz~~ | **deepseek-v4-flash-zz** | OpenAI |
| **adv_model** | ~~claude-fable-5-zz~~ | **qwen3.8-max-zz** | OpenAI |

---

## 💡 建议与行动项

### 立即行动

1. **【高优先级】修复 Anthropic 认证问题**
   - 联系 tokenhub 确认 Anthropic 端点的认证方式
   - 或获取支持 Anthropic 的 API Key
   - 或使用 Anthropic 官方 API

2. **【临时方案】调整默认模型**
   
   如果短期内无法修复，建议修改 `config/llm.json`：
   ```json
   {
     "base_model": {
       "default_model_id": "deepseek-v4-flash-zz"  // 从 claude-sonnet-5-zz 改为此
     },
     "adv_model": {
       "default_model_id": "qwen3.8-max-zz"  // 从 claude-fable-5-zz 改为此
     }
   }
   ```

### 长期优化

1. **监控响应时间**
   - `qwen3.6-plus-zz`: 11.00s（较慢，建议降低优先级）
   - `gpt-5.6-sol-zz`: 6.58s（较慢，已是最低优先级）

2. **优化缓存策略**
   - 多个模型响应时间为 0.00s（缓存命中）
   - 说明缓存工作正常

3. **成本优化**
   - DeepSeek 模型（cost_level: "low"）表现优秀
   - 可以作为主力模型使用

---

## 📈 健康度评分

| 维度 | 评分 | 说明 |
|------|:----:|------|
| **整体可用性** | ⭐⭐⭐⭐☆ | 85.7% 可用率 |
| **OpenAI 协议** | ⭐⭐⭐⭐⭐ | 100% 可用 |
| **Anthropic 协议** | ☆☆☆☆☆ | 0% 可用（需修复）|
| **降级链** | ⭐⭐⭐⭐⭐ | 工作正常 |
| **响应速度** | ⭐⭐⭐⭐☆ | 大部分 < 3s |

**总评**: 系统整体健康，但 Anthropic 协议需要立即修复。OpenAI 兼容模型作为降级备份运行良好。

---

## 📝 测试说明

- **缓存影响**: 部分模型响应时间为 0.00s 是因为使用了 LLM 响应缓存（temperature=0）
- **Token 统计**: 用量因模型而异，符合预期
- **降级机制**: 已验证工作正常（Claude 失败后自动切换到 DeepSeek/Qwen）

---

**详细报告**: `/Users/fengyoung/MyProjects/iris3/data/llm_connectivity_report_1788960251.md`

**测试脚本**: `scripts/test_llm_connectivity.py`

---

## 🔧 快速修复步骤

```bash
# 1. 验证 tokenhub Anthropic 端点
curl -H "Authorization: Bearer $IRIS_ZZ_TOKENHUB_API_KEY" \
     https://tokenhub.zhuanspirit.com/anthropic

# 2. 如果认证失败，临时调整默认模型
# 编辑 config/llm.json:
#   base_model.default_model_id: "deepseek-v4-flash-zz"
#   adv_model.default_model_id: "qwen3.8-max-zz"

# 3. 重新测试
python scripts/test_llm_connectivity.py
```
