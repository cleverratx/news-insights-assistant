class LLMError(Exception):
    pass


class LLMConfigurationError(LLMError):
    pass


class BudgetExceededError(LLMError):
    pass


class BudgetUnavailableError(LLMError):
    pass


class LLMProtocolError(LLMError):
    pass


class LLMProviderError(LLMError):
    def __init__(self, *, retryable: bool, retry_after: float = 0) -> None:
        super().__init__("Провайдер LLM недоступен или отклонил запрос")
        self.retryable = retryable
        self.retry_after = retry_after
