"""
RuleBasedPlanner 单元测试（V0.2）

覆盖：
- parse_goal：URL / 搜索关键词 / 目标文本 提取
- 内置规则：导航 → 搜索输入 → 提交搜索 → 完成判定 → 等待
- 自定义规则优先级
- goal 变化时状态重置
"""

import sys
sys.path.insert(0, ".")

import pytest
from agent.core.planner import Planner, RuleBasedPlanner, parse_goal
from agent.schema.action import Action
from agent.schema.observation import Observation
from agent.schema.snapshot import ElementInfo, Snapshot


# ═══════════════════════════════════════════════════════════════
# 构造辅助
# ═══════════════════════════════════════════════════════════════

def el(text="", element_id="", tag="", element_type="",
       placeholder="", aria_label="", attributes=None):
    return ElementInfo(
        text=text, element_id=element_id, tag=tag, element_type=element_type,
        placeholder=placeholder, aria_label=aria_label,
        attributes=attributes or {},
    )


def search_page_snapshot(url="file:///E:/test/search.html") -> Snapshot:
    """带搜索框 + 搜索按钮的页面"""
    return Snapshot(
        title="测试搜索页",
        url=url,
        inputs=[el(element_id="e1", tag="input", element_type="textbox",
                   placeholder="请输入搜索关键词")],
        buttons=[el(text="搜索", element_id="e2", tag="button", element_type="button")],
    )


def result_page_snapshot(url="file:///E:/test/search.html") -> Snapshot:
    """搜索后的结果页（含目标关键词文本 + 结果链接）"""
    return Snapshot(
        title="测试搜索页 - 结果",
        url=url,
        inputs=[el(element_id="e1", tag="input", element_type="textbox",
                   placeholder="请输入搜索关键词")],
        buttons=[el(text="搜索", element_id="e2", tag="button", element_type="button")],
        links=[el(text="北京时间（中国国家标准时间）", element_id="e3", tag="a", element_type="link")],
        texts=[el(text="已找到关于 北京时间 的结果", element_id="e4", tag="p")],
    )


# ═══════════════════════════════════════════════════════════════
# parse_goal
# ═══════════════════════════════════════════════════════════════

class TestParseGoal:

    def test_extract_url(self):
        spec = parse_goal("打开 https://example.com/search 看看")
        assert spec.url == "https://example.com/search"

    def test_extract_file_url(self):
        spec = parse_goal("打开 file:///E:/test/search.html")
        assert spec.url == "file:///E:/test/search.html"

    def test_url_strips_trailing_punctuation(self):
        spec = parse_goal("打开 https://example.com/a，然后查找 X")
        assert spec.url == "https://example.com/a"

    def test_extract_search_keyword(self):
        spec = parse_goal("查找 北京时间")
        assert spec.search_keywords == ["北京时间"]

    def test_extract_search_keyword_no_space(self):
        spec = parse_goal("搜索 人工智能")
        assert spec.search_keywords == ["人工智能"]

    def test_no_false_positive_on_搜索页(self):
        """'搜索页' 中的 '搜索' 后跟 '页'，不应被误判为搜索动词"""
        spec = parse_goal("在搜索页查找 北京时间")
        assert spec.search_keywords == ["北京时间"]

    def test_extract_quoted_target(self):
        spec = parse_goal('点击"登录"按钮')
        assert "登录" in spec.target_texts

    def test_done_keywords_merge(self):
        spec = parse_goal('查找 北京时间 并点击"详情"')
        assert spec.done_keywords == ["北京时间", "详情"]

    def test_no_goal_elements(self):
        spec = parse_goal("随便看看")
        assert spec.url is None
        assert spec.search_keywords == []
        assert spec.target_texts == []


# ═══════════════════════════════════════════════════════════════
# 内置规则
# ═══════════════════════════════════════════════════════════════

class TestBuiltinRules:

    @pytest.mark.asyncio
    async def test_plan_navigates_to_url(self):
        """未到达目标 URL 时返回 goto"""
        planner = RuleBasedPlanner()
        snap = Snapshot(title="空白页", url="about:blank")
        action = await planner.plan(snap, "打开 https://example.com/search 查找 X")
        assert action.action == "goto"
        assert action.value == "https://example.com/search"

    @pytest.mark.asyncio
    async def test_plan_done_for_pure_navigation(self):
        """纯导航任务：到达目标 URL 即完成"""
        planner = RuleBasedPlanner()
        snap = Snapshot(title="搜索", url="https://example.com/search")
        action = await planner.plan(snap, "打开 https://example.com/search")
        assert action.action == "done"

    @pytest.mark.asyncio
    async def test_plan_inputs_search_keyword(self):
        """有搜索框时输入关键词"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        action = await planner.plan(snap, "查找 北京时间")
        assert action.action == "input"
        assert action.value == "北京时间"
        assert action.target_id == "e1"

    @pytest.mark.asyncio
    async def test_plan_clicks_search_button_after_input(self):
        """已输入关键词后，点击搜索按钮提交"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        await planner.plan(snap, "查找 北京时间")   # 输入
        action = await planner.plan(snap, "查找 北京时间")  # 提交
        assert action.action == "click"
        assert action.target_id == "e2"

    @pytest.mark.asyncio
    async def test_plan_done_when_goal_satisfied(self):
        """已提交搜索且页面出现目标关键词 → done"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        await planner.plan(snap, "查找 北京时间")   # input
        await planner.plan(snap, "查找 北京时间")   # click 搜索
        result_snap = result_page_snapshot()
        action = await planner.plan(result_snap, "查找 北京时间")
        assert action.action == "done"

    @pytest.mark.asyncio
    async def test_plan_wait_when_results_not_ready(self):
        """已提交但结果未出现 → 返回 wait"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        await planner.plan(snap, "查找 北京时间")   # input
        await planner.plan(snap, "查找 北京时间")   # click 搜索
        # 结果页尚未出现关键词（无链接无文本）
        action = await planner.plan(search_page_snapshot(), "查找 北京时间")
        assert action.action == "wait"

    @pytest.mark.asyncio
    async def test_plan_clicks_result_link(self):
        """已提交搜索，页面有结果链接但无完成关键词 → 点击第一条结果"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        await planner.plan(snap, "查找 北京时间")
        await planner.plan(snap, "查找 北京时间")
        # 只有链接、无目标关键词（完成判定不满足）
        only_links = Snapshot(
            title="结果", url="file:///E:/test/search.html",
            links=[el(text="进入结果详情页", element_id="e3",
                      tag="a", element_type="link")],
        )
        action = await planner.plan(only_links, "查找 北京时间")
        assert action.action == "click"
        assert action.target_id == "e3"

    @pytest.mark.asyncio
    async def test_plan_clicks_target_text(self):
        """页面出现目标文本（引号标注）→ 点击"""
        planner = RuleBasedPlanner()
        snap = Snapshot(
            title="首页", url="https://example.com",
            buttons=[el(text="登录", element_id="e1", tag="button", element_type="button")],
        )
        action = await planner.plan(snap, '点击"登录"')
        assert action.action == "click"
        assert action.target_id == "e1"


# ═══════════════════════════════════════════════════════════════
# 自定义规则与状态重置
# ═══════════════════════════════════════════════════════════════

class TestCustomRulesAndReset:

    @pytest.mark.asyncio
    async def test_custom_rule_takes_priority(self):
        """自定义规则优先于内置规则"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        planner.add_rule(
            condition=lambda s, g: s.title == "测试搜索页",
            action_fn=lambda s: Action(action="refresh"),
        )
        action = await planner.plan(snap, "查找 北京时间")
        assert action.action == "refresh"

    @pytest.mark.asyncio
    async def test_custom_rule_falls_through_when_none(self):
        """自定义规则返回 None 时继续走内置规则"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        planner.add_rule(
            condition=lambda s, g: s.title == "测试搜索页",
            action_fn=lambda s: None,
        )
        action = await planner.plan(snap, "查找 北京时间")
        assert action.action == "input"

    @pytest.mark.asyncio
    async def test_new_goal_resets_state(self):
        """goal 变化时自动重置任务状态"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        await planner.plan(snap, "查找 北京时间")   # 变为 searched=True
        assert planner._searched is True
        action = await planner.plan(snap, "查找 人工智能")  # 新 goal → 重置
        assert action.action == "input"
        assert action.value == "人工智能"

    def test_reset_clears_all_state(self):
        planner = RuleBasedPlanner()
        planner._spec = parse_goal("查找 X")
        planner._searched = True
        planner._submitted = True
        planner.reset()
        assert planner._spec is None
        assert planner._searched is False
        assert planner._submitted is False

    @pytest.mark.asyncio
    async def test_plan_returns_none_when_no_rule_matches(self):
        """无搜索意图、无 URL、无目标文本时返回 None"""
        planner = RuleBasedPlanner()
        snap = Snapshot(title="随便", url="about:blank")
        assert await planner.plan(snap, "随便看看") is None

    @pytest.mark.asyncio
    async def test_custom_rule_exception_falls_through(self):
        """单条自定义规则异常被捕获，不中断规划（V0.2 B1）"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()

        def bad_condition(s, g):
            raise RuntimeError("boom")

        planner.add_rule(
            condition=bad_condition,
            action_fn=lambda s: Action(action="refresh"),
            name="bad-rule",
        )
        action = await planner.plan(snap, "查找 北京时间")
        # 异常规则被跳过，继续走内置规则 → input
        assert action.action == "input"

    def test_add_rule_supports_name(self):
        """add_rule 支持可读规则名，默认取函数名（V0.2 B1）"""
        planner = RuleBasedPlanner()

        def my_action(s):
            return None

        planner.add_rule(
            condition=lambda s, g: True,
            action_fn=my_action,
            name="我的规则",
        )
        assert planner._rules[0][0] == "我的规则"

        # 未指定 name 时取 action_fn.__name__
        planner.add_rule(condition=lambda s, g: True, action_fn=my_action)
        assert planner._rules[1][0] == "my_action"


# ═══════════════════════════════════════════════════════════════
# 解析增强：点击 X / 等待 X
# ═══════════════════════════════════════════════════════════════

class TestEnhancedGoalParsing:

    def test_extract_click_target_with_dash(self):
        """点击目标含分隔符（空格 + 破折号）也能完整提取"""
        spec = parse_goal("打开 https://www.baidu.com 查找 北京时间 点击 北京时间 - 百度百科")
        assert spec.url == "https://www.baidu.com"
        assert spec.search_keywords == ["北京时间"]
        assert spec.target_texts == ["北京时间 - 百度百科"]

    def test_click_target_stops_at_next_verb(self):
        """点击目标在下一个动词前截断"""
        spec = parse_goal("点击 登录 然后 等待 页面加载完成")
        assert spec.target_texts == ["登录"]
        assert spec.wait_loading is True

    def test_click_target_stops_at_halfwidth_comma(self):
        """点击目标遇半角逗号/分号截断（待解决问题 #49，中英混输）"""
        spec = parse_goal("点击 登录, 然后搜索 北京时间")
        assert spec.target_texts == ["登录"]

    def test_click_target_preserves_dotted_version(self):
        """点击目标内半角点号（版本号/日期）不被误截断（待解决问题 #49）"""
        spec = parse_goal("点击 v1.2 版本 下载")
        assert "v1.2" in spec.target_texts[0]
        date_spec = parse_goal("点击 2024.06.05 记录")
        assert "2024.06.05" in date_spec.target_texts[0]

    def test_click_target_stops_at_dotted_space(self):
        """半角点号后跟空白时视为短语边界（待解决问题 #49）"""
        spec = parse_goal("点击 登录. 然后 查找 X")
        assert spec.target_texts == ["登录"]

    def test_click_target_quoted_preferred(self):
        """点击目标含引号时优先取引号内容，不产生冗余目标"""
        spec = parse_goal('点击"登录"按钮')
        assert spec.target_texts == ["登录"]

    def test_wait_loading_phrase(self):
        """等待 页面加载完成 → wait_loading=True"""
        spec = parse_goal("等待 页面加载完成")
        assert spec.wait_loading is True
        assert spec.wait_texts == []

    def test_wait_text_condition(self):
        """等待 指定文本 → wait_texts"""
        spec = parse_goal("等待 结果加载完成")
        assert spec.wait_loading is False
        assert spec.wait_texts == ["结果加载完成"]

    def test_no_false_click_on_点击词(self):
        """普通目标文本（非点击动词）不被误判"""
        spec = parse_goal("查找 点击率 统计")
        assert spec.target_texts == []


# ═══════════════════════════════════════════════════════════════
# 规则增强：显式点击目标 + 等待条件
# ═══════════════════════════════════════════════════════════════

def baike_link_snapshot() -> Snapshot:
    """搜索结果页：包含目标链接「北京时间 - 百度百科」"""
    return Snapshot(
        title="北京时间_百度搜索",
        url="https://www.baidu.com/s?wd=北京时间",
        links=[el(text="北京时间 - 百度百科", element_id="e3", tag="a", element_type="link")],
    )


class TestClickTargetFlow:

    @pytest.mark.asyncio
    async def test_clicks_explicit_target_before_done(self):
        """页面已含关键词但目标未点击 → 点击目标而非提前 done"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        goal = "查找 北京时间 点击 北京时间 - 百度百科"
        await planner.plan(snap, goal)   # input
        await planner.plan(snap, goal)   # submit
        action = await planner.plan(baike_link_snapshot(), goal)
        assert action.action == "click"
        assert action.target_id == "e3"

    @pytest.mark.asyncio
    async def test_done_after_all_targets_clicked(self):
        """目标点击后 → 全部点击完成 → done"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        goal = "查找 北京时间 点击 北京时间 - 百度百科"
        await planner.plan(snap, goal)
        await planner.plan(snap, goal)
        baike = baike_link_snapshot()
        assert (await planner.plan(baike, goal)).action == "click"
        assert (await planner.plan(baike, goal)).action == "done"

    @pytest.mark.asyncio
    async def test_not_done_when_keyword_present_but_target_missing(self):
        """关键词已出现但目标未点击（目标也不在页面）→ 不得 done"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        goal = "查找 北京时间 点击 北京时间 - 百度百科"
        await planner.plan(snap, goal)
        await planner.plan(snap, goal)
        keyword_only = Snapshot(
            title="北京时间_百度搜索",
            url="https://www.baidu.com/s?wd=北京时间",
            texts=[el(text="北京时间", element_id="e4", tag="p")],
        )
        action = await planner.plan(keyword_only, goal)
        assert action is not None
        assert action.action != "done"


class TestWaitConditions:

    @pytest.mark.asyncio
    async def test_wait_loading_blocks_progress(self):
        """等待页面加载完成：页面 loading 时返回 wait"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        goal = "查找 北京时间 等待 页面加载完成"
        await planner.plan(snap, goal)   # input
        await planner.plan(snap, goal)   # submit
        snap.loading = True
        action = await planner.plan(snap, goal)
        assert action.action == "wait"
        snap.loading = False
        # 加载完成后不再因等待条件阻塞
        action = await planner.plan(snap, goal)
        assert action.action == "wait"  # 兜底等待（无结果无目标）

    @pytest.mark.asyncio
    async def test_wait_text_then_click_target(self):
        """等待文本未出现 → wait；出现后 → 继续点击目标"""
        planner = RuleBasedPlanner()
        goal = "查找 北京时间 等待 结果出现 点击 北京时间 - 百度百科"
        snap = search_page_snapshot()
        await planner.plan(snap, goal)   # input
        await planner.plan(snap, goal)   # submit
        # 等待文本未出现 → wait
        assert (await planner.plan(snap, goal)).action == "wait"
        # 等待文本出现 + 目标链接出现 → 点击目标
        result = Snapshot(
            title="结果",
            url=snap.url,
            texts=[el(text="结果出现", element_id="e9", tag="p")],
            links=[el(text="北京时间 - 百度百科", element_id="e3", tag="a",
                      element_type="link")],
        )
        action = await planner.plan(result, goal)
        assert action.action == "click"
        assert action.target_id == "e3"

    @pytest.mark.asyncio
    async def test_wait_gives_up_after_max_tries(self):
        """等待条件持续不满足 → 超过上限后放弃（返回 None）"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        goal = "查找 北京时间 等待 结果出现"
        await planner.plan(snap, goal)   # input
        await planner.plan(snap, goal)   # submit
        action = None
        for _ in range(planner._WAIT_MAX_TRIES + 1):
            action = await planner.plan(snap, goal)
        assert action is None

    @pytest.mark.asyncio
    async def test_normalized_text_matching(self):
        """文本归一化：目标含分隔符也能匹配页面元素"""
        planner = RuleBasedPlanner()
        snap = Snapshot(
            title="搜索",
            url="https://example.com",
            links=[el(text="北京时间-百度百科", element_id="e3", tag="a",
                      element_type="link")],
        )
        action = await planner.plan(snap, "点击 北京时间 - 百度百科")
        assert action.action == "click"
        assert action.target_id == "e3"


# ═══════════════════════════════════════════════════════════════
# 失败回滚：on_action_result（待解决问题 #1）
# ═══════════════════════════════════════════════════════════════

class TestFailureRollback:
    """动作执行失败时回滚乐观状态，避免跳过失败步骤或误判任务完成"""

    @pytest.mark.asyncio
    async def test_input_failure_retries_input(self):
        """输入失败回滚 _searched → 下一轮仍规划 input"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        action = await planner.plan(snap, "查找 北京时间")   # 乐观置位 _searched
        assert action.action == "input"
        planner.on_action_result(action, Observation.fail(error="输入失败"))
        assert planner._searched is False
        action2 = await planner.plan(snap, "查找 北京时间")
        assert action2.action == "input"

    @pytest.mark.asyncio
    async def test_submit_failure_retries_submit(self):
        """提交失败回滚 _submitted → 下一轮仍规划提交 click"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        await planner.plan(snap, "查找 北京时间")   # input
        action = await planner.plan(snap, "查找 北京时间")  # submit（乐观置位）
        assert action.action == "click"
        planner.on_action_result(action, Observation.fail(error="提交失败"))
        assert planner._submitted is False
        action2 = await planner.plan(snap, "查找 北京时间")
        assert action2.action == "click"
        assert action2.target_id == "e2"

    @pytest.mark.asyncio
    async def test_target_click_failure_keeps_submitted(self):
        """目标点击失败仅回滚该目标，不影响已提交状态"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        goal = "查找 北京时间 点击 北京时间 - 百度百科"
        await planner.plan(snap, goal)   # input
        await planner.plan(snap, goal)   # submit（_submitted=True）
        baike = baike_link_snapshot()
        action = await planner.plan(baike, goal)  # 点击目标
        assert action.action == "click"
        planner.on_action_result(action, Observation.fail(error="点击失败"))
        assert planner._submitted is True      # 提交状态不受影响
        assert planner._clicked == set()       # 目标已回滚
        action2 = await planner.plan(baike, goal)
        assert action2.action == "click"       # 重新点击目标，而非 done
        assert action2.target_id == "e3"

    @pytest.mark.asyncio
    async def test_success_keeps_state(self):
        """执行成功状态保持（无回滚）"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        action = await planner.plan(snap, "查找 北京时间")   # input
        planner.on_action_result(action, Observation.ok())
        assert planner._searched is True
        action2 = await planner.plan(snap, "查找 北京时间")
        assert action2.action == "click"       # 跳过 input 直接提交

    @pytest.mark.asyncio
    async def test_target_click_failure_not_premature_done(self):
        """目标点击失败后不得提前 done（_should_finish 依赖 _clicked）"""
        planner = RuleBasedPlanner()
        snap = search_page_snapshot()
        goal = "查找 北京时间 点击 北京时间 - 百度百科"
        await planner.plan(snap, goal)
        await planner.plan(snap, goal)
        baike = baike_link_snapshot()
        action = await planner.plan(baike, goal)
        assert action.action == "click"
        planner.on_action_result(action, Observation.fail(error="点击失败"))
        action2 = await planner.plan(baike, goal)
        assert action2.action == "click"       # 而非 done

    def test_base_planner_on_action_result_is_noop(self):
        """Planner 基类 on_action_result 默认为 no-op，不抛异常"""
        planner = Planner()
        planner.on_action_result(
            Action(action="click", target_id="e1"), Observation.fail(error="x"),
        )
