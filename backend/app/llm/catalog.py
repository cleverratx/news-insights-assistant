from dataclasses import dataclass

from app.llm.models import AllowedModel


@dataclass(frozen=True)
class ProviderModel:
    provider_id: str
    max_input_tokens: int


PROVIDER_MODELS = {
    AllowedModel.GEMMA: ProviderModel("google/gemma-4-26b-a4b-it", 262144),
    AllowedModel.GLM: ProviderModel("z-ai/glm-5.3-flash", 1048576),
    AllowedModel.QWEN: ProviderModel("qwen/qwen3.6-35b-a3b", 262144),
    AllowedModel.DEEPSEEK: ProviderModel("deepseek/deepseek-v4.1-flash", 1048576),
    AllowedModel.QWEN_VL: ProviderModel("qwen/qwen3-vl-30b-a3b-instruct", 262144),
    AllowedModel.KIMI: ProviderModel("moonshotai/kimi-k2.6", 262144),
}
