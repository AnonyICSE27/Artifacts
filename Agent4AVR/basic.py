from typing import Optional, List, Dict, Any, Union
from dataclasses import dataclass, asdict, field


@dataclass
class Patch:
    patch: List[str]  # SEARCH/REPLACE diff patch
    file_paths: Optional[List[str]] = None
    original_file_contents: Optional[List[Optional[str]]] = None
    patched_file_contents: Optional[List[Optional[str]]] = None
    index_in_patch_space: Optional[int] = None
    additional_info: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Message:
    role: str
    content: Optional[str] = None
    logprobs: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]):
        return cls(
            role=d["role"],
            content=d.get("content", None),
            logprobs=d.get("logprobs", None),
        )

    @classmethod
    def _make_content(cls, role: str, content: str, **kwargs):
        content = content.format_map(kwargs) if kwargs else content
        return cls(role=role, content=content)

    @classmethod
    def system(cls, content: str, **kwargs):
        return cls._make_content(role="system", content=content, **kwargs)

    @classmethod
    def user(cls, content: str, **kwargs):
        return cls._make_content(role="user", content=content, **kwargs)

    @classmethod
    def assistant(cls, content: str, **kwargs):
        return cls._make_content(role="assistant", content=content, **kwargs)


@dataclass(frozen=True)
class MessageList:
    messages: List[Message] = field(default_factory=list)

    def __getitem__(self, idx):
        return self.messages[idx]

    def append(self, msg: Message):
        self.messages.append(msg)

    def aslist(self) -> List[Dict[str, str]]:
        return [asdict(m) for m in self.messages]


@dataclass(frozen=True)
class LLMQueryRecord:
    id: str
    model_name: str
    model_config: Dict[str, Any]
    start_at: float
    finish_at: float
    query: Union[str, MessageList]
    response: Union[str, Message, List[Message]]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    ext_info: Dict[str, Any] = None
