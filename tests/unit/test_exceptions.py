"""iris.core.exceptions 统一异常体系单元测试。

覆盖：
- 全项目 19 个自定义异常类（15 RuntimeError 系 + StorageError + 2 ValueError 系 + WriteGuardError）均挂在 IrisError 之下；
- 各异常仍保留原标准库父类（RuntimeError / ValueError / PermissionError），既有 except 不受影响；
- StorageError 在 iris.core / iris.core.storage / iris.core.exceptions 三处导出为同一对象。
"""

from __future__ import annotations

import pytest

from iris.complex_input.docx_adapter import DocxAdapterError
from iris.complex_input.pdf_adapter import PdfAdapterError
from iris.complex_input.video_adapter import VideoAdapterError
from iris.config.loader import ConfigError
from iris.config.secrets import KeychainError
from iris.core.exceptions import IrisError, IrisRuntimeError, IrisValueError, StorageError
from iris.core.locks import FileLockError
from iris.core.write_guard import WriteGuardError
from iris.feed._chat_fetcher import ChatFetchError
from iris.feishu.chat_digest import ChatDigestError
from iris.feishu.client import FeishuClientError
from iris.feishu.doc_convert import FeishuDocConvertError
from iris.ingest.pdf_extractor import PDFExtractorError
from iris.llm.model_manager import ModelManagerError
from iris.llm.provider import LLMProviderError
from iris.retrieval.embedder import EmbedderError
from iris.retrieval.vector_index import VectorIndexModelMismatchError
from iris.trello.client import TrelloClientError
from iris.utils.validation import ValidationError

# RuntimeError 系（15 个业务异常 + StorageError）
RUNTIME_ERRORS = [
    LLMProviderError,
    ModelManagerError,
    FileLockError,
    FeishuDocConvertError,
    ChatDigestError,
    KeychainError,
    FeishuClientError,
    PDFExtractorError,
    VectorIndexModelMismatchError,
    EmbedderError,
    ChatFetchError,
    PdfAdapterError,
    TrelloClientError,
    VideoAdapterError,
    DocxAdapterError,
    StorageError,
]

# ValueError 系
VALUE_ERRORS = [ConfigError, ValidationError]

# PermissionError 系
PERMISSION_ERRORS = [WriteGuardError]

ALL_ERRORS = RUNTIME_ERRORS + VALUE_ERRORS + PERMISSION_ERRORS


class TestBaseHierarchy:
    """基类自身的层级关系。"""

    def test_runtime_base_is_iris_and_runtime(self):
        assert issubclass(IrisRuntimeError, IrisError)
        assert issubclass(IrisRuntimeError, RuntimeError)

    def test_value_base_is_iris_and_value(self):
        assert issubclass(IrisValueError, IrisError)
        assert issubclass(IrisValueError, ValueError)

    def test_storage_error_is_runtime(self):
        assert issubclass(StorageError, IrisRuntimeError)


class TestAllErrorsUnderIrisError:
    """全部自定义异常都能被 except IrisError 捕获。"""

    @pytest.mark.parametrize("exc_cls", ALL_ERRORS, ids=lambda c: c.__name__)
    def test_is_iris_error(self, exc_cls):
        assert issubclass(exc_cls, IrisError)

    def test_count(self):
        # 15 RuntimeError 系业务异常 + StorageError + 2 ValueError 系 + 1 PermissionError 系
        assert len(ALL_ERRORS) == 19


class TestStdlibParentsPreserved:
    """原标准库父类保留在 MRO 中，既有 except RuntimeError/ValueError/PermissionError 不受影响。"""

    @pytest.mark.parametrize("exc_cls", RUNTIME_ERRORS, ids=lambda c: c.__name__)
    def test_runtime_errors(self, exc_cls):
        assert issubclass(exc_cls, IrisRuntimeError)
        assert issubclass(exc_cls, RuntimeError)
        assert not issubclass(exc_cls, ValueError)

    @pytest.mark.parametrize("exc_cls", VALUE_ERRORS, ids=lambda c: c.__name__)
    def test_value_errors(self, exc_cls):
        assert issubclass(exc_cls, IrisValueError)
        assert issubclass(exc_cls, ValueError)
        assert not issubclass(exc_cls, RuntimeError)

    @pytest.mark.parametrize("exc_cls", PERMISSION_ERRORS, ids=lambda c: c.__name__)
    def test_permission_errors(self, exc_cls):
        assert issubclass(exc_cls, PermissionError)
        assert not issubclass(exc_cls, IrisRuntimeError)
        assert not issubclass(exc_cls, IrisValueError)


class TestStorageErrorIdentity:
    """StorageError 三个导入路径必须是同一个类对象（否则 except 会漏捕）。"""

    def test_same_object(self):
        from iris.core import StorageError as core_storage_error
        from iris.core.exceptions import StorageError as exc_storage_error
        from iris.core.storage import StorageError as storage_storage_error

        assert core_storage_error is exc_storage_error
        assert storage_storage_error is exc_storage_error


class TestCatchBehavior:
    """运行期抛出/捕获行为。"""

    def test_config_error_caught_by_iris_error(self):
        with pytest.raises(IrisError):
            raise ConfigError("x")

    def test_config_error_caught_by_value_error(self):
        with pytest.raises(ValueError):
            raise ConfigError("x")

    def test_config_error_not_caught_by_runtime_error(self):
        with pytest.raises(ConfigError):
            try:
                raise ConfigError("x")
            except RuntimeError:  # pragma: no cover — 不应走到这里
                pytest.fail("ConfigError 不应被 except RuntimeError 捕获")

    def test_llm_provider_error_caught_by_runtime_error(self):
        with pytest.raises(RuntimeError):
            raise LLMProviderError("x")

    def test_write_guard_error_caught_by_permission_error_and_iris_error(self):
        with pytest.raises(PermissionError):
            raise WriteGuardError("x")
        with pytest.raises(IrisError):
            raise WriteGuardError("x")

    def test_message_preserved(self):
        err = ConfigError("配置缺字段")
        assert str(err) == "配置缺字段"
