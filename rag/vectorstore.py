"""RAG 벡터 스토어 초기화 및 관리 모듈.

ChromaDB를 사용하여 게임 디자인 문서를 임베딩하고
유사 문서 검색 기능을 제공합니다.
"""
import logging
from pathlib import Path

from dotenv import load_dotenv
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv()
logger = logging.getLogger("indie_game_advisor")

_DOCS_DIR = Path(__file__).parent / "documents"
_PERSIST_DIR = Path(__file__).parent / "chroma_db"

_embeddings = OpenAIEmbeddings(model="text-embedding-3-small")


def _build_vectorstore() -> Chroma:
    """문서 디렉토리에서 Chroma 벡터 스토어를 생성합니다."""
    logger.info("RAG 벡터 스토어 구축 중... (최초 실행 시 약 30초 소요)")

    loader = DirectoryLoader(
        str(_DOCS_DIR),
        glob="**/*.txt",
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8"},
        show_progress=False,
    )
    docs = loader.load()
    logger.info("로드된 문서 수: %d", len(docs))

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=600,
        chunk_overlap=80,
        separators=["\n\n", "\n", "。", ".", " "],
    )
    chunks = splitter.split_documents(docs)
    logger.info("청크 수: %d", len(chunks))

    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=_embeddings,
        persist_directory=str(_PERSIST_DIR),
    )
    logger.info("Chroma 벡터 스토어 구축 완료 및 저장됨.")
    return vectorstore


def _load_vectorstore() -> Chroma:
    """저장된 Chroma 인덱스를 로드합니다."""
    logger.info("저장된 RAG 벡터 스토어 로드 중...")
    return Chroma(
        persist_directory=str(_PERSIST_DIR),
        embedding_function=_embeddings,
    )


def get_vectorstore() -> Chroma:
    """벡터 스토어를 반환합니다. 없으면 새로 구축합니다."""
    if _PERSIST_DIR.exists() and any(_PERSIST_DIR.iterdir()):
        try:
            return _load_vectorstore()
        except Exception as e:
            logger.warning("기존 인덱스 로드 실패, 재구축합니다: %s", e)

    return _build_vectorstore()


# 모듈 로드 시 벡터 스토어 초기화
vectorstore = get_vectorstore()
retriever = vectorstore.as_retriever(search_kwargs={"k": 4})
