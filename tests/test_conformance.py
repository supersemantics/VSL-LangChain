from vsl_core.conformance.suite import run_conformance_suite

from vsl_langchain import LangChainAdapter


def test_langchain_adapter_is_conformant():
    failures = run_conformance_suite(LangChainAdapter())
    assert failures == []
