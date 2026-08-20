"""
SnapshotGenerator iframe 支持测试（V1.0 子计划 A-0 验证路径模型）

使用真实 Playwright Chromium（本地 file:// + 数据 URL），覆盖：
- 三层 iframe 递归提取：主页面 / 第二层 / 第三层元素全部出现
- frame_path 正确生成（空元组 = 主页面，非空 = iframe 链）
- target_id 全局唯一（跨 frame 不冲突）
- 消歧：无 id/无 name iframe 用位置索引；重复 id 用位置消歧
- 主页面行为零回归：frame_path=() 元素与旧版一致

运行需已安装 Playwright 浏览器：
    python -m playwright install chromium
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from agent.browser.snapshot import SnapshotGenerator
from agent.browser.playwright import BrowserTool
from agent.core.executor import Executor
from agent.schema.action import click as act_click, input_text as act_input

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "iframe"
TOP = FIXTURE_DIR / "iframe_toplevel.html"


@pytest.fixture
async def page():
    """启动真实 Chromium 页面，载入三层 iframe 顶层 fixture。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(TOP.as_uri())
        # 等待 iframe 网络（SAP 式嵌套）加载完成：第二层在顶层内，第三层在第二层内
        fl1 = page.frame_locator("iframe#frame-level1")
        await fl1.locator("#l1-btn").first.wait_for(state="attached")
        fl2 = fl1.frame_locator("iframe#frame-level2")
        await fl2.locator("#l2-btn").first.wait_for(state="attached")
        yield page
        await browser.close()


def _by(snap, element_type: str, element_id: str):
    """按 element_type 列表 + element_id 找元素"""
    items = getattr(snap, element_type)
    for it in items:
        if it.element_id == element_id:
            return it
    return None


@pytest.mark.asyncio
async def test_three_level_elements_visible(page):
    """三层 iframe 内元素全部被提取（层级正确）"""
    snap = await SnapshotGenerator(page).generate()
    texts = {it.element_id: it.text for it in snap.texts}
    buttons = {it.element_id: it.text for it in snap.buttons}

    # 主页面
    assert "顶层页面" in texts.values()
    assert "顶层按钮" in buttons.values()
    # 第二层
    assert "第二层 iframe" in texts.values()
    assert "第二层按钮" in buttons.values()
    # 第三层（最深 iframe）
    assert "第三层 iframe" in texts.values()
    assert "第三层按钮" in buttons.values()


@pytest.mark.asyncio
async def test_frame_path_generation(page):
    """frame_path 正确：主页面=( )，二/三层为 iframe 链"""
    snap = await SnapshotGenerator(page).generate()

    # 主页面元素（按钮）
    top_btn = next(b for b in snap.buttons if b.text == "顶层按钮")
    assert top_btn.frame_path == ()

    # 第二层元素
    l1_btn = next(b for b in snap.buttons if b.text == "第二层按钮")
    assert l1_btn.frame_path == ("#frame-level1",)

    # 第三层元素
    l2_btn = next(b for b in snap.buttons if b.text == "第三层按钮")
    assert l2_btn.frame_path == ("#frame-level1", "#frame-level2")


@pytest.mark.asyncio
async def test_target_id_unique_across_frames(page):
    """target_id（element_id）跨 frame 全局唯一"""
    snap = await SnapshotGenerator(page).generate()
    ids = [it.element_id for it in snap.get_interactive_elements()]
    assert len(ids) == len(set(ids)), f"存在重复 element_id: {ids}"


@pytest.mark.asyncio
async def test_no_id_iframe_positional_disambiguation(page):
    """无 id/无 name 的 iframe 用位置索引消歧（父内第一个额外 iframe）"""
    snap = await SnapshotGenerator(page).generate()
    xtra_btn = next(
        (b for b in snap.buttons if b.text == "额外按钮"), None
    )
    assert xtra_btn is not None
    # 顶层两个 iframe：frame-level1（id）与无 id iframe → 位置 index=1
    assert xtra_btn.frame_path == ("iframe >> nth=1",)


@pytest.mark.asyncio
async def test_duplicate_id_iframe_disambiguation(page):
    """重复 id 的 iframe：第一个用 id，第二个用位置索引消歧"""
    snap = await SnapshotGenerator(page).generate()
    dup_btns = [b for b in snap.buttons if b.text == "重复按钮"]

    # extra 内两个重复 id iframe → 各 1 个按钮
    assert len(dup_btns) == 2

    # 进入 extra 的路径：无 id iframe → 位置 nth=1
    base = ("iframe >> nth=1",)
    # extra 内两个 iframe（重复 id dup-f）：第一个 id，第二个位置 nth=1
    frames = sorted(dup_btns, key=lambda b: b.text)
    paths = {b.frame_path for b in dup_btns}
    assert paths == {
        base + ("#dup-f",),
        base + ("iframe >> nth=1",),
    }


@pytest.mark.asyncio
async def test_main_frame_zero_regression(page):
    """主页面元素 frame_path=()，与旧版（无 iframe）行为一致"""
    snap = await SnapshotGenerator(page).generate()
    main_elements = [
        it for it in snap.get_interactive_elements() if it.frame_path == ()
    ]
    # 主页面应有 1 按钮 + 1 输入
    assert sum(1 for it in main_elements if it.tag == "button") == 1
    assert sum(1 for it in main_elements if it.tag == "input") >= 1


# ═══════════════════════════════════════════════════════════════
# A-2 执行可达：BrowserTool / Executor 穿透 iframe 定位
# ═══════════════════════════════════════════════════════════════

def _element_by(snap, element_type: str, frame_path: tuple):
    """在 Snapshot 中按 frame_path 找元素（返回第一个）"""
    for it in getattr(snap, element_type):
        if it.frame_path == frame_path:
            return it
    return None


@pytest.mark.asyncio
async def test_browser_tool_click_in_third_level_iframe(page):
    """BrowserTool.click 穿透三层 iframe 点击第三层按钮"""
    tool = BrowserTool(page)
    # 第三层按钮 frame_path：#frame-level1 → #frame-level2
    result = await tool.click("#l2-btn", frame_path=("#frame-level1", "#frame-level2"))
    assert result.success
    await page.wait_for_timeout(100)
    status = page.frame_locator("iframe#frame-level1").frame_locator(
        "iframe#frame-level2"
    ).locator("#l2-status")
    assert await status.inner_text() == "已被穿透点击"


@pytest.mark.asyncio
async def test_browser_tool_input_in_third_level_iframe(page):
    """BrowserTool.input 穿透三层 iframe 向第三层输入框填入文本"""
    tool = BrowserTool(page)
    result = await tool.input(
        "#l2-input",
        "hello-frame",
        frame_path=("#frame-level1", "#frame-level2"),
    )
    assert result.success
    inp = page.frame_locator("iframe#frame-level1").frame_locator(
        "iframe#frame-level2"
    ).locator("#l2-input")
    assert await inp.input_value() == "hello-frame"


@pytest.mark.asyncio
async def test_executor_click_third_level_by_target_id(page):
    """端到端：Snapshot → target_id → Executor 穿透 iframe 点击第 2 层按钮"""
    snap = await SnapshotGenerator(page).generate()
    # 第 2 层按钮（frame=("#frame-level1",)）
    target = _element_by(snap, "buttons", ("#frame-level1",))
    assert target is not None, "Snapshot 未找到第二层按钮"

    tool = BrowserTool(page)
    executor = Executor(tool)
    obs = await executor.execute(
        act_click(target="x", target_id=target.element_id), snapshot=snap
    )
    assert obs.success
    await page.wait_for_timeout(100)
    # 验证穿透执行到了第三层：修改的是深层 iframe 内按钮对应的状态
    # （点击第二层按钮，但第二层无交互；我们点击第三层来验证最深穿透）
    target3 = _element_by(snap, "buttons", ("#frame-level1", "#frame-level2"))
    assert target3 is not None
    obs3 = await executor.execute(
        act_click(target="x", target_id=target3.element_id), snapshot=snap
    )
    assert obs3.success
    status = page.frame_locator("iframe#frame-level1").frame_locator(
        "iframe#frame-level2"
    ).locator("#l2-status")
    assert await status.inner_text() == "已被穿透点击"


@pytest.mark.asyncio
async def test_executor_input_third_level_by_target_id(page):
    """端到端：Snapshot → target_id → Executor 穿透 iframe 填写深层输入框"""
    snap = await SnapshotGenerator(page).generate()
    target = _element_by(snap, "inputs", ("#frame-level1", "#frame-level2"))
    assert target is not None, "Snapshot 未找到第三层输入框"

    tool = BrowserTool(page)
    executor = Executor(tool)
    obs = await executor.execute(
        act_input(target="x", text="rpa-ok", target_id=target.element_id),
        snapshot=snap,
    )
    assert obs.success
    inp = page.frame_locator("iframe#frame-level1").frame_locator(
        "iframe#frame-level2"
    ).locator("#l2-input")
    assert await inp.input_value() == "rpa-ok"


@pytest.mark.asyncio
async def test_llm_parse_action_target_id_keeps_frame_path(page):
    """回归 #1：LLM 输出经 parse_action_dict → Executor → BrowserTool，
    iframe 元素 frame_path 不被本地注入的 selector 降级为主页面"""
    from agent.prompts.planner import parse_action_dict

    snap = await SnapshotGenerator(page).generate()
    target = _element_by(snap, "inputs", ("#frame-level1", "#frame-level2"))
    assert target is not None, "Snapshot 未找到第三层输入框"

    # 模拟 LLM 输出：只给 target_id（selector 由 parse_action_dict 本地注入）
    action = parse_action_dict(
        {"action": "input", "target_id": target.element_id, "value": "rpa-ok"},
        snap,
    )
    # 确认注入点存在：模型未提供 selector，但 parse_action_dict 已写入本地可信 selector
    assert action.params.get("selector") == target.selector

    tool = BrowserTool(page)
    executor = Executor(tool)
    obs = await executor.execute(action, snapshot=snap)
    assert obs.success
    inp = page.frame_locator("iframe#frame-level1").frame_locator(
        "iframe#frame-level2"
    ).locator("#l2-input")
    assert await inp.input_value() == "rpa-ok"