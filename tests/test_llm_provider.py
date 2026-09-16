from ecommerce_agent.config import Settings
from ecommerce_agent.llm import RuleBasedGateway, build_gateway


def test_model_provider_can_select_offline_gateway():
    gateway = build_gateway(Settings(llm_provider="mock", deepseek_api_key="not-used"))
    assert isinstance(gateway, RuleBasedGateway)
    assert gateway.is_configured is False


def test_unknown_model_provider_fails_fast():
    try:
        build_gateway(Settings(llm_provider="unknown"))
    except ValueError as exc:
        assert "LLM_PROVIDER" in str(exc)
    else:
        raise AssertionError("unknown provider should fail")
