import asyncio
from types import SimpleNamespace

import pytest

from recent_mail_fetcher.exitcodes import EXIT_AUTH, EXIT_TOOL_FAILURE, FailError
from recent_mail_fetcher.invoker import call_tool


class HttpErr(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.response = SimpleNamespace(status_code=status)


class Stub:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append(dict(args))
        o = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(o, BaseException):
            raise o
        return o


def ok():
    return SimpleNamespace(isError=False, content=[SimpleNamespace(text="[]")])


def err(text):
    return SimpleNamespace(isError=True, content=[SimpleNamespace(text=text)])


async def _nosleep(_):
    pass


def run(stub, args=None):
    return asyncio.run(call_tool(stub, "t", args or {"count": 5}, sleep=_nosleep))


def test_success():
    s = Stub(ok())
    assert run(s).isError is False
    assert len(s.calls) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retry_then_success(status):
    s = Stub(HttpErr(status), HttpErr(status), ok())
    run(s)
    assert len(s.calls) == 3


def test_retry_exhausted_exit4():
    s = Stub(HttpErr(503))
    with pytest.raises(FailError) as e:
        run(s)
    assert e.value.code == EXIT_TOOL_FAILURE
    assert len(s.calls) == 3


def test_read_timeout_retried():
    s = Stub(TimeoutError("read timed out"), ok())
    run(s)
    assert len(s.calls) == 2


@pytest.mark.parametrize("status", [401, 403])
def test_auth_no_retry(status):
    s = Stub(HttpErr(status))
    with pytest.raises(FailError) as e:
        run(s)
    assert e.value.code == EXIT_AUTH
    assert len(s.calls) == 1


def test_permission_denied_result_exit2():
    s = Stub(err("PERMISSION_DENIED: no access"))
    with pytest.raises(FailError) as e:
        run(s)
    assert e.value.code == EXIT_AUTH
    assert len(s.calls) == 1


def test_rejected_args_retry_without_count():
    s = Stub(err("invalid argument 'count'"), ok())
    run(s, {"count": 5, "label": "INBOX"})
    assert s.calls == [{"count": 5, "label": "INBOX"}, {"label": "INBOX"}]


def test_rejected_args_still_failing_exit4():
    s = Stub(err("invalid argument"))
    with pytest.raises(FailError) as e:
        run(s)
    assert e.value.code == EXIT_TOOL_FAILURE
    assert len(s.calls) == 2


def test_success_false_body_exit4_with_valid_fields(caplog):
    body = (
        '{"errorType": "invalidArguments", "missing": ["query"], "success": false, '
        '"validFields": ["cursor","max_results","query"]}'
    )
    s = Stub(SimpleNamespace(isError=False, content=[SimpleNamespace(text=body)]))
    with pytest.raises(FailError) as ei:
        run(s, {"query": None})
    assert ei.value.code == EXIT_TOOL_FAILURE
    assert "validFields" in str(ei.value)


def test_success_false_retries_without_count():
    body = '{"success": false, "message": "bad"}'
    bad = SimpleNamespace(isError=False, content=[SimpleNamespace(text=body)])
    s = Stub(bad, ok())
    run(s)
    assert s.calls == [{"count": 5}, {}]
