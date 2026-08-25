import json
import os
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from app.core.config import get_settings
from app.services.llm_client import BedrockClient, get_llm_client


class BedrockClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {
            "LLM_PROVIDER": os.environ.get("LLM_PROVIDER"),
            "AWS_REGION": os.environ.get("AWS_REGION"),
            "BEDROCK_SUMMARY_MODEL": os.environ.get("BEDROCK_SUMMARY_MODEL"),
            "BEDROCK_REPORT_MODEL": os.environ.get("BEDROCK_REPORT_MODEL"),
            "BEDROCK_API_KEY": os.environ.get("BEDROCK_API_KEY"),
            "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY"),
        }
        os.environ["LLM_PROVIDER"] = "bedrock"
        os.environ["AWS_REGION"] = "ap-northeast-2"
        os.environ["BEDROCK_SUMMARY_MODEL"] = "amazon.nova-lite-v1:0"
        os.environ["BEDROCK_REPORT_MODEL"] = "amazon.nova-pro-v1:0"
        os.environ["BEDROCK_API_KEY"] = "test-key"
        os.environ.pop("GEMINI_API_KEY", None)
        get_settings.cache_clear()

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    def test_get_llm_client_selects_bedrock(self) -> None:
        with patch(
            "app.services.bedrock_runtime.create_bedrock_runtime_client",
            return_value=MagicMock(),
        ):
            client = get_llm_client()
        self.assertIsInstance(client, BedrockClient)

    def test_classify_parses_converse_json(self) -> None:
        runtime = MagicMock()
        runtime.converse.return_value = {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "category": "충전 인프라",
                                    "confidence": 0.9,
                                    "keywords": [{"name": "OCPP", "confidence": 0.8}],
                                },
                                ensure_ascii=False,
                            )
                        }
                    ]
                }
            }
        }
        with patch(
            "app.services.bedrock_runtime.create_bedrock_runtime_client",
            return_value=runtime,
        ):
            client = BedrockClient()
            result = client.classify_post_content("OCPP 업데이트", "충전 표준 개정")
        self.assertEqual(result["category"], "충전 인프라")
        runtime.converse.assert_called()
        kwargs = runtime.converse.call_args.kwargs
        self.assertEqual(kwargs["modelId"], "amazon.nova-lite-v1:0")
        self.assertTrue(kwargs["system"])

    def test_daily_report_uses_report_model(self) -> None:
        runtime = MagicMock()
        runtime.converse.return_value = {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "summary": "전기차·충전 동향을 정리했습니다.",
                                    "recommendations": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    ]
                }
            }
        }
        with patch(
            "app.services.bedrock_runtime.create_bedrock_runtime_client",
            return_value=runtime,
        ):
            client = BedrockClient()
            result = client.generate_daily_report(
                [{"id": "1", "title": "충전 요금", "summary": "인하", "board": "trusted"}],
                date(2026, 8, 25),
                edition={"name": "전기차·충전", "slug": "ev", "topics": ["충전"]},
            )
        self.assertIn("전기차", result["summary"])
        self.assertEqual(runtime.converse.call_args.kwargs["modelId"], "amazon.nova-pro-v1:0")


if __name__ == "__main__":
    unittest.main()
