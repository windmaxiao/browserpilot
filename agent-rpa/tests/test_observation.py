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


def test_observation_with_data():
    obs = Observation.ok(data={"key": "value"}, extra_field="hello")
    assert obs.data["key"] == "value"
    assert obs.data["extra_field"] == "hello"
