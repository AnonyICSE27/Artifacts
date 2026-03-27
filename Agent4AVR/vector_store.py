from os import getenv
from sys import getsizeof
from copy import deepcopy
from inspect import getsource
from json import loads as json_loads
from typing import Optional, Any, List, Dict
from langchain_core.load import dumps
from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain.storage import LocalFileStore
from langchain.embeddings import CacheBackedEmbeddings
from langchain_text_splitters import CharacterTextSplitter
from .rs_utils import _ilog, _split_list_as_chunks
from .utils import _dlog


def _make_embedding_model(embedding_model_config):
    def _import_openai_embeddings():
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings

    print_kwargs = {**embedding_model_config, "api_key": "..."}
    _ilog(f"Creating {embedding_model_config['type']} embed model, cfg={print_kwargs}")

    embedding_model_config = embedding_model_config.copy()
    emb_type = embedding_model_config.pop("type")
    local_cache_path = embedding_model_config.pop("local_cache_path")
    namespace = embedding_model_config.pop("namespace")

    base_url = embedding_model_config.get("base_url", None)
    if base_url and base_url.startswith("$"):
        if not (base_url := getenv(base_url[1:], None)):
            raise ValueError(f"Env var {base_url[1:]} is not set")
        embedding_model_config["base_url"] = base_url

    emb_cls = {
        "openai": _import_openai_embeddings,
    }[emb_type]()

    underlying_embeddings = emb_cls(**embedding_model_config)
    local_store = LocalFileStore(local_cache_path)
    return CacheBackedEmbeddings.from_bytes_store(
        underlying_embeddings,
        local_store,
        namespace=namespace,
        key_encoder="sha256",
    )


class SimpleVectorStore:
    def __init__(
        self,
        embed_model_config: Dict[str, Any],
        documents: Optional[List[Dict[str, Any]]] = None,
        load_filename: Optional[str] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        **kwargs,
    ):
        super().__init__()
        _ilog(f"Building vector store...")
        _dlog(f">>>> embeddings: {embed_model_config['type']}")
        _dlog(f">>>> len(doc_txts): {len(documents) if documents else 'NA'}")
        _dlog(f">>>> chunk_size: {chunk_size}")
        _dlog(f">>>> chunk_overlap: {chunk_overlap}")
        self.__embed_model_config = embed_model_config
        self.__embedding_model = _make_embedding_model(embed_model_config)
        self.__documents = documents
        self.__queries = []
        if load_filename is not None:
            self.__base_vector_store = InMemoryVectorStore.load(
                path=load_filename,
                embedding=self.__embedding_model,
            )
        else:
            self.__base_vector_store = InMemoryVectorStore(
                embedding=self.__embedding_model
            )
        if documents is not None:
            documents = deepcopy(documents)
            if chunk_size is not None and chunk_overlap is not None:
                text_splitter = CharacterTextSplitter.from_tiktoken_encoder(
                    encoding_name="cl100k_base",  # text-embedding-3-small
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    separator="\n",
                )
            else:

                class _NullTextSplitter:
                    def split_documents(self, documents):
                        return documents

                text_splitter = _NullTextSplitter()
                del _NullTextSplitter

            document_texts_parts = list(
                _split_list_as_chunks(
                    origin_list=documents,
                    chunk_size=100,
                )
            )
            assert sum(len(p) for p in document_texts_parts) == len(documents)
            added_count = 0
            steps = len(document_texts_parts)
            for i, document_list in enumerate(document_texts_parts, start=0):
                _ilog(f">>>> [{i}/{steps}] Added {added_count} doc to vector store")
                docs = [
                    Document(page_content=dt.pop("text"), metadata={**dt})
                    for dt in document_list
                ]
                _dlog(f">>>>>> Before split: {len(docs)} documents")
                docs = text_splitter.split_documents(docs)
                _dlog(f">>>>>> After split: {len(docs)} documents")
                self.__base_vector_store.add_documents(docs)
                added_count += len(document_list)
            assert added_count == len(documents)
            _ilog(f">>>> Added {added_count} documents to vector store")

    def get_record(self) -> Dict[str, Any]:
        """
        Get config, documents, queries and store.
        Used to calculate token cost.
        """
        config = {**self.__embed_model_config, "api_key": "***"}
        documents = self.__documents.copy()
        queries = self.__queries.copy()
        store_str = dumps(self.__base_vector_store.store)
        _dlog(f"Size of vector store ~= {getsizeof(store_str) / 1024 / 1024:.2f} MB")
        store = json_loads(store_str)  # may be big&slow, but don't care here
        return {
            "config": config,
            "documents": documents,
            "queries": queries,
            "store": store,
        }

    def save(self, path):
        self.__base_vector_store.dump(path)

    def search(self, query, k, filter=None) -> List[Document]:
        def _get_source(obj) -> str | None:
            try:
                return getsource(obj)
            except Exception:
                return None

        self.__queries.append(
            {
                "query": query,
                "k": k,
                "filter": {
                    "type": str(type(filter)),
                    "str": str(filter),
                    "source": _get_source(filter),
                },
            }
        )

        return self.__base_vector_store.similarity_search(
            query=query, filter=filter, k=k
        )
