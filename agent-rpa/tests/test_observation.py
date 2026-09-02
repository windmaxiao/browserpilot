"""
Observation Schema 单元测试
"""

from agent.schema.observation import Observation


def test_observation_ok():
    obs = Observation.ok(url="https://example.com", title="Test", page_changed=True)
    assert obs.success is True
    assert obs.url == "https://example.com"
    assert obs.title == "Test"
    assert obs.page_changed is True
    assert obs.is_error is False


def test_observation_fail():
    obs = Observation.fail("出错了", url="https://example.com")
    assert obs.success is False
    assert obs.error == "出错了"
    assert obs.is_error is True
    assert obs.has_error is True


def test_observation_fail_explicit_data():
    """fail() 显式 data 参数，不再产生 data['data'] 嵌套"""
    obs = Observation.fail("出错了", url="https://example.com", data={"detail": "原因"})
    assert obs.error == "出错了"
    assert obs.data["detail"] == "原因"
    assert "data" not in obs.data


def test_observation_fail_unknown_kwarg_raises():
    """fail() 不支持的 kwargs 直接抛 TypeError（待解决问题 #42，防拼错静默并入 data）"""
    import pytest

    with pytest.raises(TypeError):
        Observation.fail("出错了", detail="原因")


def test_observation_with_data():
    obs = Observation.ok(data={"key": "value"})
    assert obs.data["key"] == "value"
    assert obs.data == {"key": "value"}


def test_observation_ok_unknown_kwarg_raises():
    """ok() 不支持的 kwargs 直接抛 TypeError（待解决问题 #42）"""
    import pytest

    with pytest.raises(TypeError):
        Observation.ok(data={"key": "value"}, extra_field="hello")
