"""
Prompt 序列化测试（V0.3 阶段 B）

覆盖：
- serialize_snapshot 不暴露 selector / HTML / href / Cookie
- URL 脱敏（fragment 移除 + 敏感参数掩码）
- 元素数量 / 文本长度截断与显式标记
- 同一输入序列化结果稳定
- find_element_by_id 只命中当前 Snapshot 的可见元素
- serialize_history 历史窗口与字段白名单
"""

from agent.prompts.planner import (
    find_element_by_id,
    sanitize_url,
    serialize_element,
    serialize_history,
    serialize_snapshot,
)
from agent.schema.snapshot import ElementInfo, Snapshot


def _el(
    element_id: str,
    *,
    text: str = "",
    element_type: str = "button",
    tag: str = "button",
    selector: str = "",
    aria_label: str = "",
    placeholder: str = "",
    attributes: dict | None = None,
) -> ElementInfo:
    return ElementInfo(
        text=text,
        element_id=element_id,
        tag=tag,
        element_type=element_type,
        selector=selector,
        aria_label=aria_label,
        placeholder=placeholder,
        attributes=attributes or {},
    )


def _snapshot(*, url: str = "https://example.com/login", title: str = "登录",
              page_type: str = "login", elements: list[ElementInfo] | None = None) -> Snapshot:
    els = elements or []
    return Snapshot(
        title=title,
        url=url,
        page_type=page_type,
        buttons=[e for e in els if e.element_type == "button"],
        inputs=[e for e in els if e.element_type == "textbox"],
        links=[e for e in els if e.element_type == "link"],
    )


class TestSerializeSnapshot:
    def test_contains_only_interactive_elements(self):
        snap = _snapshot(elements=[
            _el("e0", element_type="textbox", placeholder="用户名"),
            _el("e1", element_type="button", text="登录"),
        ])
        data = serialize_snapshot(snap)
        assert data["title"] == "登录"
        assert data["page_type"] == "login"
        # get_interactive_elements() 顺序：buttons + inputs + links + selects
        assert [e["id"] for e in data["elements"]] == ["e1", "e0"]
        assert data["elements"][0]["text"] == "登录"
        assert data["elements"][1]["placeholder"] == "用户名"

    def test_no_selector_or_html_in_context(self):
        snap = _snapshot(elements=[
            _el("e0", element_type="button", text="登录",
                selector="#btn-login", attributes={"data-testid": "login-btn"}),
        ])
        data = serialize_snapshot(snap)
        rendered = str(data)
        # 不暴露执行用 selector 与完整 HTML 形态
        assert "selector" not in data["elements"][0]
        assert "#btn-login" not in rendered
        assert "<button" not in rendered
        # 白名单属性保留
        assert data["elements"][0]["attributes"] == {"data-testid": "login-btn"}

    def test_excludes_non_whitelisted_attributes(self):
        el = _el("e0", element_type="textbox",
                 attributes={"data-testid": "u", "onclick": "evil()", "value": "secret"})
        assert serialize_element(el)["attributes"] == {"data-testid": "u"}

    def test_element_count_truncation(self):
        els = [_el(f"e{i}") for i in range(3)]
        data = serialize_snapshot(_snapshot(elements=els), max_elements=2)
        assert len(data["elements"]) == 2
        assert data["elements_truncated"] is True

    def test_no_truncation_flag_when_within_limit(self):
        els = [_el("e0"), _el("e1")]
        data = serialize_snapshot(_snapshot(elements=els), max_elements=5)
        assert "elements_truncated" not in data

    def test_text_truncation_marked(self):
        el = _el("e0", text="a" * 50)
        data = serialize_element(el, max_text_length=10)
        assert data["text"].startswith("a" * 10)
        assert "(truncated)" in data["text"]

    def test_serialization_is_stable(self):
        snap = _snapshot(url="https://example.com/login?token=abc", elements=[
            _el("e0", element_type="textbox", placeholder="用户名"),
            _el("e1", element_type="button", text="登录"),
        ])
        assert serialize_snapshot(snap) == serialize_snapshot(snap)


class TestSanitizeUrl:
    def test_removes_fragment(self):
        assert sanitize_url("https://x.com/page#section") == "https://x.com/page"

    def test_masks_sensitive_query_params(self):
        url = "https://x.com/api?token=abc123&q=hello&code=xyz&page=2"
        assert sanitize_url(url) == "https://x.com/api?token=***&q=hello&code=***&page=2"

    def test_masks_case_insensitive_params(self):
        assert sanitize_url("https://x.com/a?API_KEY=sec") == "https://x.com/a?API_KEY=***"

    def test_masks_combined_with_fragment(self):
        url = "https://x.com/a?session=abc#top"
        assert sanitize_url(url) == "https://x.com/a?session=***"

    def test_invalid_url_returned_unchanged(self):
        assert sanitize_url("not a url") == "not a url"

    def test_empty_url(self):
        assert sanitize_url("") == ""


class TestFindElementById:
    def test_hits_visible_element(self):
        el = _el("e2", element_type="button", text="提交")
        snap = _snapshot(elements=[_el("e1"), el])
        assert find_element_by_id(snap, "e2") is el

    def test_miss_returns_none(self):
        snap = _snapshot(elements=[_el("e1")])
        assert find_element_by_id(snap, "e99") is None

    def test_empty_id_returns_none(self):
        snap = _snapshot(elements=[_el("e1")])
        assert find_element_by_id(snap, "") is None

    def test_ignores_invisible_elements(self):
        # 序列化只基于 Snapshot 的可交互列表；不在列表内的元素不可命中
        hidden = _el("e9", element_type="button")
        snap = Snapshot(title="t", url="u", buttons=[], texts=[hidden])
        assert find_element_by_id(snap, "e9") is None


class TestSerializeHistory:
    def _action(self, action: str = "click", target: str = "登录", target_id: str = "e1"):
        return type("A", (), {"action": action, "target": target, "target_id": target_id})()

    def _obs(self, success: bool = True, url: str = "https://x.com", data: dict | None = None):
        return type("O", (), {"success": success, "url": url, "data": data or {}})()

    def test_keeps_only_recent_window(self):
        history = [
            {"step": i, "action": self._action(), "observation": self._obs(url=f"https://x.com/{i}")}
            for i in range(1, 7)
        ]
        out = serialize_history(history, max_items=3)
        assert len(out) == 3
        assert [e["url"] for e in out] == [f"https://x.com/{i}" for i in (4, 5, 6)]

    def test_keeps_whitelisted_fields_only(self):
        history = [{
            "step": 1,
            "action": self._action("input", "搜索框", "e2"),
            "observation": self._obs(False, "https://x.com/err",
                                     data={"screenshot": "base64longdata"}),
        }]
        out = serialize_history(history)
        entry = out[0]
        assert entry["action"] == "input"
        assert entry["target"] == "搜索框"
        assert entry["target_id"] == "e2"
        assert entry["success"] is False
        assert entry["url"] == "https://x.com/err"
        # 敏感/冗余内容不进入上下文
        assert "screenshot" not in entry
        assert "base64longdata" not in str(out)

    def test_empty_history(self):
        assert serialize_history([]) == []
