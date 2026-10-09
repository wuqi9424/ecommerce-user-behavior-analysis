"""Small transport contract. No provider has access to analytics callables."""
from dataclasses import dataclass, field
from typing import Protocol, Any


class ConfigurationError(RuntimeError): pass
class ProviderError(RuntimeError): pass


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: Any


@dataclass
class ModelTurn:
    calls: list[ToolCall] = field(default_factory=list)
    final_text: str | None = None
    metadata: dict = field(default_factory=dict)
    # Opaque continuation items remain inside the adapter, never in saved trace.


class Provider(Protocol):
    metadata: dict
    redaction_secrets: tuple[str, ...]
    def complete(self, *, question:str, system_prompt:str, tools:list[dict],
                 tool_outputs:list[dict], allow_tools:bool) -> ModelTurn: ...
