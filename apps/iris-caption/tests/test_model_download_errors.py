import errno
from urllib.error import URLError, HTTPError
import pytest
from iris_caption.models import _url
from iris_caption.model_dialog import _failure_message


def test_official_cdn_allowed_but_lookalike_rejected():
    assert _url('https://us.aws.cdn.hf.co/path')
    for url in ('https://us.aws.cdn.hf.co.evil.test/path', 'http://us.aws.cdn.hf.co/path',
                'https://user:secret@us.aws.cdn.hf.co/path'):
        with pytest.raises(ValueError):
            _url(url)


def test_safe_actionable_install_errors():
    assert '超时' in _failure_message(URLError(TimeoutError('signed-token-secret')))
    assert '403' in _failure_message(HTTPError('https://secret',403,'secret',{},None))
    assert '空间不足' in _failure_message(OSError(errno.ENOSPC,'secret'))
    assert '校验值' in _failure_message(ValueError('Model integrity check failed'))
    assert 'secret' not in _failure_message(RuntimeError('secret'))
