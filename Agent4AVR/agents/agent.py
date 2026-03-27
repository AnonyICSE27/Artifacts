from os import makedirs
from os.path import isfile
from datetime import datetime
from json import loads as json_loads, dumps as json_dumps
from dataclasses import dataclass, is_dataclass, asdict
from typing import Any, List, Dict, Literal
from .. import rs_utils as rsu
from ..model import LLMQueryRecorder, LLMQueryRecord, _record_llm_query
from ..utils import _DebugLog


_agent_ask_recorder = None


@dataclass
class AgentAskRecord:
    agent_name: str
    agent_itendifier: str
    agent_output: Any


class AgentAskRecorder:
    def __init__(self):
        self.__records: List[AgentAskRecord] = []

    @property
    def records(self):
        return self.__records

    def __enter__(self):
        global _agent_ask_recorder
        self.__old_recorder = _agent_ask_recorder
        _agent_ask_recorder = self
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        global _agent_ask_recorder
        _agent_ask_recorder = self.__old_recorder
        return False

    def append(self, agent_name: str, agent_identifier: str, agent_output: Any):
        self.__records.append(
            AgentAskRecord(
                agent_name=agent_name,
                agent_itendifier=agent_identifier,
                agent_output=agent_output,
            )
        )


def _record_agent_ask(agent_name: str, agent_identifier: str, agent_output: Any):
    global _agent_ask_recorder
    if _agent_ask_recorder is not None:
        _agent_ask_recorder.append(agent_name, agent_identifier, agent_output)


class Agent:
    _agent_id_pool: Dict[str, int] = {}

    @classmethod
    def _make_agent_itendifier(cls, agent_name: str):
        if agent_name not in cls._agent_id_pool:
            cls._agent_id_pool[agent_name] = 0
        agent_id = cls._agent_id_pool[agent_name]
        agent_itendifier = f"{agent_name}@{agent_id}"
        cls._agent_id_pool[agent_name] += 1
        return agent_itendifier

    def __init__(
        self, name, output_dir: str | None = None, cache_dir: str | None = None
    ) -> None:
        self.__name = name
        self.__output_dir = output_dir
        self.__cache_dir = cache_dir
        self.__itendifier = self._make_agent_itendifier(name)
        self.dlog = _DebugLog(prefix=f"Agent[{self.__itendifier}]", fback=2)

    @property
    def name(self) -> str:
        return self.__name

    @property
    def itendifier(self) -> str:
        return self.__itendifier

    @property
    def _output_dir(self) -> str | None:
        return self.__output_dir

    @property
    def _cache_dir(self) -> str | None:
        return self.__cache_dir

    def _load_cache(
        self,
        key: str,
        scope: Literal["local", "global"] = "local",
    ) -> Any | None:
        _dir = self._cache_dir if scope == "global" else self._output_dir
        if _dir is None:
            self.dlog.w(f"Cache dir is None, skip loading cache for key `{key}`")
            return None
        cache_path = f"{_dir}/_CACHE_{key}.json"
        if not isfile(cache_path):
            self.dlog.w(f"Cache file `{cache_path}` does not exist, skip loading")
            return None
        self.dlog.i(f"Loading cache from `{cache_path}`")
        cached: dict = rsu._load_json(cache_path)
        if llm_query_records := cached.get("_llm_query_records"):
            for r in llm_query_records:
                r["ext_info"]["_load_from_cache"] = True
                r["ext_info"]["_cache_key"] = key
                r["ext_info"]["_cache_file"] = cache_path
                _record_llm_query(**r)
        return cached["_cached_data"]

    def _save_cache(
        self,
        data: Any,
        key: str,
        llm_query_records: List[LLMQueryRecord] | None = None,
        scope: Literal["local", "global"] = "local",
        **kwargs,
    ):
        if isinstance(llm_query_records, list):
            llm_query_records = llm_query_records.copy()

        ext_info = {}
        for kw_key, kw_value in kwargs.items():
            if is_dataclass(kw_value):
                ext_info[kw_key] = asdict(kw_value)
            elif rsu._is_json_serializable(kw_value):
                ext_info[kw_key] = json_loads(json_dumps(kw_value))
            else:
                raise ValueError(f"Ext info `{kw_key}` is not JSON serializable")
            del kw_key, kw_value

        _dir = self._cache_dir if scope == "global" else self._output_dir
        if _dir is None:
            self.dlog.w(f"Cache dir is None, skip saving cache for key `{key}`")
            return None
        cache_path = f"{_dir}/_CACHE_{key}.json"
        if isfile(cache_path):
            self.dlog.w(f"Cache file `{cache_path}` already exists, skip saving")
            raise FileExistsError(f"Cache file `{cache_path}` already exists")
        self.dlog.i(f"Saving cache to `{cache_path}`")
        cached_obj = {
            "_cached_data": data,
            "_cache_time": datetime.now().timestamp(),
            "_cache_path": cache_path,
            "_agent_name": self.__name,
            "_agent_itendifier": self.__itendifier,
            "_agent_output_dir": self.__output_dir,
            "_agent_cache_dir": self.__cache_dir,
            "_llm_query_records": [asdict(r) for r in llm_query_records or []],
            "_ext_info": ext_info,
        }
        rsu._save_as_json(cached_obj, cache_path, indent=2, auto_mkdir=True)

    def _ask_impl(self, input):
        pass

    @rsu._abstractmethod
    def _save_output(self, output: Any, output_dir: str):
        pass

    def ask(self, input):
        self.ilog(f"Asking agent(`{self.__itendifier}`) >>>> start <<<<")

        with LLMQueryRecorder() as llm_recorder, AgentAskRecorder() as agent_recorder:
            output = self._ask_impl(input)
        if not hasattr(output, "llm_query_records"):
            raise ValueError("`llm_query_records` is required but None")
        if not hasattr(output, "asked_agent_outputs"):
            raise ValueError("`asked_agent_outputs` is required but None")
        output.llm_query_records = llm_recorder.records
        output.asked_agent_outputs = agent_recorder.records
        _record_agent_ask(self.__name, self.__itendifier, output)

        self.ilog(f"Asking agent(`{self.__itendifier}`) >>>> finished <<<<")
        return output

    def save_output(self, output: Any):
        self.ilog(f"Saving output to `{self._output_dir}`")
        if self._output_dir is None:
            raise ValueError("`output_dir` is required but None")
        makedirs(self._output_dir, exist_ok=True)
        self._save_output(output, self._output_dir)

    def _log(self, log_fn, *msg, fback, **kwargs):
        log_fn(f"Agent[{self.__itendifier}]", *msg, fback=fback, **kwargs)

    def ilog(self, *args, **kwargs):
        self._log(rsu._ilog, *args, fback=3, **kwargs)

    def wlog(self, *args, **kwargs):
        self._log(rsu._wlog, *args, fback=3, **kwargs)

    def elog(self, *args, **kwargs):
        self._log(rsu._elog, *args, fback=3, **kwargs)

    def flog(self, *args, **kwargs):
        self._log(rsu._flog, *args, fback=3, **kwargs)
