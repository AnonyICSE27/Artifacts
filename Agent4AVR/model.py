import time
from os import getenv, path
from typing import Optional, List, Dict, Union, Any
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception,
)
from . import rs_utils as rsu
from .utils import _dlog
from .basic import Message, MessageList, LLMQueryRecord

try:
    import openai
except ImportError:
    openai = None


class ContextLengthExceededException(Exception):
    pass


_model_query_recorder = None


class LLMQueryRecorder:
    def __init__(self, propagate_to_parent: bool = False) -> None:
        self.records: List[LLMQueryRecord] = []
        self.__propagate_to_parent = propagate_to_parent

    def __enter__(self):
        global _model_query_recorder
        self.__old_recorder = _model_query_recorder
        _model_query_recorder = self
        if self.__propagate_to_parent:
            self.__parent_recorder = self.__old_recorder
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        global _model_query_recorder
        _model_query_recorder = self.__old_recorder
        return False

    def _record(self, record: LLMQueryRecord):
        self.records.append(record)
        if self.__propagate_to_parent and self.__parent_recorder is not None:
            self.__parent_recorder._record(record)

    def append(
        self,
        model_name: str,
        model_config: Dict[str, Any],
        start_at: float,
        finish_at: float,
        query: Union[str, MessageList],
        response: Union[str, Message],
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        **ext_info,
    ):
        self._record(
            LLMQueryRecord(
                id=f"{model_name}-{int(start_at * 1000)}-{int(finish_at * 1000)}",
                model_name=model_name,
                model_config=model_config,
                start_at=start_at,
                finish_at=finish_at,
                query=query,
                response=response,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                ext_info=ext_info.copy(),
            )
        )


def _record_llm_query(
    model_name: str,
    model_config: Dict[str, Any],
    start_at: float,
    finish_at: float,
    query: Union[str, MessageList],
    response: Union[str, Message, List[Message]],
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    id: str = None,
    **ext_info,
):
    if _model_query_recorder is not None:
        # copy the query and response to avoid modification
        if isinstance(query, MessageList):
            query = MessageList(query.messages.copy())
        if isinstance(response, list):
            response = response.copy()
        if len(ext_info) == 1 and "ext_info" in ext_info:
            ext_info = ext_info["ext_info"].copy()

        _model_query_recorder.append(
            model_name,
            model_config.copy(),
            start_at,
            finish_at,
            query,
            response,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            **ext_info,
        )


class Model:
    @classmethod
    def make(cls, **kwargs):
        model_cls = cls
        if cls == Model:
            model_type = kwargs.pop("model_type")
            model_cls = eval(f"{model_type}Model")
        if "base_url" in kwargs and kwargs["base_url"].startswith("$"):
            if not (base_url := getenv(kwargs["base_url"][1:], None)):
                raise ValueError(f"{kwargs['base_url'][1:]} is not set")
            else:
                kwargs["base_url"] = base_url
        print_kwargs = {**kwargs, "api_key": "..."}
        rsu._ilog(f"Creating {model_cls}, config={print_kwargs}")
        return model_cls(**kwargs)

    @property
    def name(self) -> Optional[str]:
        return self._get_config().get("model_name")

    @rsu._abstractmethod
    def _get_config(self) -> dict:
        pass

    @rsu._abstractmethod
    def _ask_impl(self, messages: MessageList, **kwargs) -> Message:
        pass

    def ask(self, messages: Union[MessageList, List[Message]], **kwargs) -> Message:
        if not isinstance(messages, MessageList):
            messages = MessageList(messages)
        return self._ask_impl(messages, **kwargs)


class OpenAIModel(Model):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        api_version: str = None,
        use_azure: bool = False,
        official_config: dict = None,
        **kwargs,
    ):
        assert openai is not None, "Install openai first: `pip install openai`"
        self.__base_url = base_url
        self.__api_key = api_key
        self.__api_version = api_version
        self.__use_azure = use_azure
        self.__model_name = model_name
        self.__ext_kwargs = kwargs.copy()
        self.__official_config = official_config or {}
        if not self.__use_azure:
            self.__openai_client = openai.Client(
                base_url=self.__base_url,
                api_key=self.__api_key,
            )
        else:
            self.__openai_client = openai.AzureOpenAI(
                azure_endpoint=self.__base_url,
                api_version=self.__api_version,
                api_key=self.__api_key,
            )

    def as_langchain_model(self):
        from langchain_openai import ChatOpenAI

        chat_openai = ChatOpenAI(
            openai_api_base=self.__base_url,
            openai_api_key=self.__api_key,
            model_name=self.__model_name,
            **self.__ext_kwargs,
        )
        _original_chat_completion_create = chat_openai.client.create

        def _hook_chat_completion_create(**kwargs):
            start_at = time.time()
            openai_messages = kwargs["messages"]
            openai_response = _original_chat_completion_create(**kwargs)
            finish_at = time.time()

            messages = MessageList([Message.from_dict(m) for m in openai_messages])
            response = Message.from_dict(
                {
                    **openai_response.choices[0].message.to_dict(),
                    "logprobs": (
                        openai_response.choices[0].logprobs.to_dict()
                        if openai_response.choices[0].logprobs is not None
                        else None
                    ),
                }
            )
            _record_llm_query(
                model_name=self.__model_name,
                model_config=self._get_config(),
                start_at=start_at,
                finish_at=finish_at,
                query=messages,
                response=response,
                prompt_tokens=openai_response.usage.prompt_tokens,
                completion_tokens=openai_response.usage.completion_tokens,
                total_tokens=openai_response.usage.total_tokens,
                _kwargs=kwargs,
                _original_openai_response=openai_response.to_dict(),
            )

            return openai_response

        # Hook openai.chat.completions.create
        chat_openai.client.create = _hook_chat_completion_create

        return chat_openai

    def _get_config(self) -> dict:
        return {
            "model_type": "OpenAI",
            "base_url": "...",
            "api_key": "...",
            "api_version": "...",
            "use_azure": self.__use_azure,
            "model_name": self.__model_name,
            **self.__ext_kwargs,
        }

    def _ask_impl(self, messages: MessageList, **kwargs) -> Message:
        def _not_context_length_exceeded(ex, no_log=False):
            _log_fn = (lambda *args, **kwargs: ...) if no_log else _dlog.w

            if (
                isinstance(ex, openai.BadRequestError)
                and ex.code == "context_length_exceeded"
            ):
                _log_fn(f"Context is too long, stop retrying...")
                return False
            elif (  # For DeepSeek-V3
                isinstance(ex, openai.BadRequestError)
                and ex.code == "invalid_request_error"
                and "This model's maximum context length is 65536 tokens. However, you requested"
                in ex.message
            ):
                _log_fn(f"Context is too long, stop retrying...")
                return False
            elif (  # For Aliyun
                isinstance(ex, openai.BadRequestError)
                and ex.code == "invalid_parameter_error"
                and "Range of input length should be [1," in ex.message
            ):
                _log_fn(f"Context is too long, stop retrying...")
                return False
            elif (  # For vLLM
                isinstance(ex, openai.BadRequestError)
                and "This model's maximum context length is" in ex.message
                and "tokens. However, you requested" in ex.message
            ):
                _log_fn(f"Context is too long, stop retrying...")
                return False
            elif isinstance(  # For OpenRouter
                ex, openai.BadRequestError
            ) and "Please reduce the length" in str(ex):
                _log_fn(f"Context is too long, stop retrying...")
                return False
            elif (
                isinstance(ex, openai.BadRequestError)  # For OpenRouter-SiliconFlow
                and "number of input tokens" in str(ex)
                and "has exceeded max_seq_len" in str(ex)
            ):
                _log_fn(f"Context is too long, stop retrying...")
                return False

            return True

        @retry(
            wait=wait_random_exponential(min=1, max=60 * 30),
            stop=stop_after_attempt(30),
            retry=retry_if_exception(_not_context_length_exceeded),
            reraise=True,
        )
        def _try_query(messages: MessageList):
            try:
                start_at = time.time()
                openai_response = self.__openai_client.chat.completions.create(
                    model=self.__model_name,
                    messages=messages.aslist(),
                    **{**self.__ext_kwargs, **kwargs},  # kwargs cover ext_kwargs
                )
                response = Message.from_dict(
                    {
                        **openai_response.choices[0].message.to_dict(),
                        "logprobs": (
                            openai_response.choices[0].logprobs.to_dict()
                            if openai_response.choices[0].logprobs is not None
                            else None
                        ),
                    }
                )
                finish_at = time.time()
                return response, start_at, finish_at, openai_response
            except Exception as ex:
                rsu._wlog(f"OpenAI API error: {ex}, retrying...")
                raise ex

        if "n" in kwargs:
            raise ValueError("`n` is not supported")

        if any(m in self.__model_name for m in {"o3-mini", "kimi-k2.5", "gpt-5-mini"}):
            if "temperature" in kwargs:
                del kwargs["temperature"]
                _dlog.w("'temperature' is not supported with this model, ignored")

        if not isinstance(messages, MessageList):
            raise ValueError(
                f"`messages` must be a `MessageList`, but got {type(messages)}"
            )

        try:
            response, start_at, finish_at, openai_response = _try_query(messages)
        except Exception as ex:
            if not _not_context_length_exceeded(ex, no_log=True):
                raise ContextLengthExceededException() from ex
            else:
                raise ex

        _record_llm_query(
            model_name=self.__model_name,
            model_config=self._get_config(),
            start_at=start_at,
            finish_at=finish_at,
            query=messages,
            response=response,
            prompt_tokens=openai_response.usage.prompt_tokens,
            completion_tokens=openai_response.usage.completion_tokens,
            total_tokens=openai_response.usage.total_tokens,
            _kwargs=kwargs,
            _original_openai_response=openai_response.to_dict(),
        )

        return response
