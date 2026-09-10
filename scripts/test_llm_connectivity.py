#!/usr/bin/env python
"""LLM 模型连通性测试工具

测试所有配置的 LLM 模型的连通性、响应时间和基本功能。

使用方法：
    python scripts/test_llm_connectivity.py
    python scripts/test_llm_connectivity.py --role base_model
    python scripts/test_llm_connectivity.py --model claude-sonnet-5-zz
"""

import sys
import time
from pathlib import Path
from typing import Dict, Any
import argparse

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from iris.config.loader import load_config_bundle
from iris.llm.service import LLMService
from iris.llm.provider import LLMProviderError


class ModelTester:
    """模型连通性测试器"""

    def __init__(self):
        self.config = load_config_bundle(project_root)
        self.llm_service = LLMService(self.config)
        self.test_prompt = "请用一句话介绍自己。"
        self.results = []

    def test_model(self, role: str, model_id: str, model_config: Dict[str, Any]) -> Dict[str, Any]:
        """测试单个模型"""
        result = {
            "role": role,
            "model_id": model_id,
            "display_name": model_config.get("display_name", model_id),
            "channel": model_config.get("channel", "N/A"),
            "provider": model_config.get("provider", "N/A"),
            "priority": model_config.get("priority", 0),
            "multimodal": model_config.get("multimodal", False),
            "status": "未测试",
            "response_time": 0.0,
            "response_text": "",
            "error": "",
            "tokens": {"prompt": 0, "completion": 0},
        }

        try:
            print("  测试中... ", end="", flush=True)

            start_time = time.time()

            # 使用 force_model 强制使用特定模型
            response = self.llm_service.generate(
                prompt=self.test_prompt,
                force_model=model_config.get("model"),
                temperature=0.0,
                max_tokens=100,
            )

            elapsed = time.time() - start_time

            result["status"] = "✅ 成功"
            result["response_time"] = elapsed
            result["response_text"] = response.text[:100]  # 只保留前100字符
            result["tokens"]["prompt"] = response.prompt_tokens
            result["tokens"]["completion"] = response.completion_tokens

            print(f"✅ {elapsed:.2f}s")

        except LLMProviderError as e:
            result["status"] = "❌ 失败"
            result["error"] = str(e)[:200]  # 只保留前200字符
            print(f"❌ {str(e)[:50]}")

        except Exception as e:
            result["status"] = "⚠️  异常"
            result["error"] = str(e)[:200]
            print(f"⚠️  {str(e)[:50]}")

        return result

    def test_all_models(self, role_filter: str = None, model_filter: str = None):
        """测试所有模型"""
        print("=" * 80)
        print("LLM 模型连通性测试")
        print("=" * 80)
        print(f"测试提示词: {self.test_prompt}")
        print()

        models_config = self.config.llm["models"]

        for role_name, role_config in models_config.items():
            # 角色过滤
            if role_filter and role_name != role_filter:
                continue

            print(f"\n### {role_name.upper()} ###")
            print(f"默认模型: {role_config['default_model_id']}")
            print()

            models = role_config["models"]

            # 按优先级排序
            sorted_models = sorted(
                models.items(),
                key=lambda x: x[1].get("priority", 0),
                reverse=True
            )

            for model_id, model_config in sorted_models:
                # 模型过滤
                if model_filter and model_id != model_filter:
                    continue

                display_name = model_config.get("display_name", model_id)
                priority = model_config.get("priority", 0)

                print(f"[{priority:3d}] {model_id}")
                print(f"      {display_name}")

                result = self.test_model(role_name, model_id, model_config)
                self.results.append(result)

        print()
        self.print_summary()

    def print_summary(self):
        """打印测试摘要"""
        print("=" * 80)
        print("测试摘要")
        print("=" * 80)

        total = len(self.results)
        success = sum(1 for r in self.results if "✅" in r["status"])
        failed = sum(1 for r in self.results if "❌" in r["status"])
        error = sum(1 for r in self.results if "⚠️" in r["status"])

        print(f"\n总计: {total} 个模型")
        print(f"  ✅ 成功: {success} 个 ({success/total*100:.1f}%)")
        print(f"  ❌ 失败: {failed} 个 ({failed/total*100:.1f}%)")
        print(f"  ⚠️  异常: {error} 个 ({error/total*100:.1f}%)")

        # 按协议分组统计
        print("\n按协议统计:")
        providers = {}
        for r in self.results:
            provider = r["provider"]
            if provider not in providers:
                providers[provider] = {"total": 0, "success": 0}
            providers[provider]["total"] += 1
            if "✅" in r["status"]:
                providers[provider]["success"] += 1

        for provider, stats in sorted(providers.items()):
            rate = stats["success"] / stats["total"] * 100 if stats["total"] > 0 else 0
            print(f"  {provider:<12} {stats['success']}/{stats['total']} ({rate:.1f}%)")

        # 成功的模型列表
        if success > 0:
            print("\n✅ 可用模型:")
            for r in self.results:
                if "✅" in r["status"]:
                    print(f"  - {r['model_id']:<35} {r['response_time']:.2f}s  "
                          f"{r['tokens']['prompt']}+{r['tokens']['completion']} tokens")

        # 失败的模型列表
        if failed > 0 or error > 0:
            print("\n❌ 不可用模型:")
            for r in self.results:
                if "❌" in r["status"] or "⚠️" in r["status"]:
                    error_msg = r["error"][:60] + "..." if len(r["error"]) > 60 else r["error"]
                    print(f"  - {r['model_id']:<35} {error_msg}")

        # 响应时间排名（仅成功的）
        successful = [r for r in self.results if "✅" in r["status"]]
        if len(successful) > 1:
            print("\n⚡ 响应速度排名 (Top 5):")
            sorted_by_time = sorted(successful, key=lambda x: x["response_time"])[:5]
            for i, r in enumerate(sorted_by_time, 1):
                print(f"  {i}. {r['model_id']:<35} {r['response_time']:.2f}s")

        print()

    def generate_report(self, output_file: str = None):
        """生成详细报告"""
        if not output_file:
            output_file = f"llm_connectivity_report_{int(time.time())}.md"

        output_path = project_root / "data" / output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# LLM 模型连通性测试报告\n\n")
            f.write(f"**测试时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"**测试提示词**: {self.test_prompt}\n\n")

            # 总体统计
            f.write("## 总体统计\n\n")
            total = len(self.results)
            success = sum(1 for r in self.results if "✅" in r["status"])
            failed = sum(1 for r in self.results if "❌" in r["status"])

            f.write(f"- 总模型数: {total}\n")
            f.write(f"- 成功: {success} ({success/total*100:.1f}%)\n")
            f.write(f"- 失败: {failed} ({failed/total*100:.1f}%)\n\n")

            # 详细结果
            f.write("## 详细测试结果\n\n")

            current_role = None
            for r in self.results:
                if r["role"] != current_role:
                    current_role = r["role"]
                    f.write(f"\n### {current_role.upper()}\n\n")

                f.write(f"#### {r['model_id']}\n\n")
                f.write(f"- **状态**: {r['status']}\n")
                f.write(f"- **显示名称**: {r['display_name']}\n")
                f.write(f"- **通道**: {r['channel']}\n")
                f.write(f"- **协议**: {r['provider']}\n")
                f.write(f"- **优先级**: {r['priority']}\n")
                f.write(f"- **多模态**: {'是' if r['multimodal'] else '否'}\n")

                if "✅" in r["status"]:
                    f.write(f"- **响应时间**: {r['response_time']:.2f}s\n")
                    f.write(f"- **Token 用量**: {r['tokens']['prompt']} (prompt) + "
                           f"{r['tokens']['completion']} (completion)\n")
                    f.write(f"- **响应预览**: {r['response_text']}\n")
                else:
                    f.write(f"- **错误信息**: {r['error']}\n")

                f.write("\n")

        print(f"📄 详细报告已保存: {output_path}")
        return output_path


def main():
    parser = argparse.ArgumentParser(description="LLM 模型连通性测试")
    parser.add_argument("--role", help="只测试指定角色的模型 (base_model/adv_model)")
    parser.add_argument("--model", help="只测试指定的模型ID")
    parser.add_argument("--report", action="store_true", help="生成详细报告")

    args = parser.parse_args()

    tester = ModelTester()
    tester.test_all_models(role_filter=args.role, model_filter=args.model)

    if args.report:
        tester.generate_report()


if __name__ == "__main__":
    main()
