"""TTS 连播与人工暂停/恢复的回归测试。

这些用例会打到 `fire_sight.alerting.tts`，用假引擎替掉 pyttsx3：
真实 SAPI 的行为由 `.runtime/_probe_p5_tts_rootcause.py` 的实测数据背书
（runAndWait 只播第一条 5.79s、后面两条 0.02s 且 completed=False；
startLoop+endLoop 三条各 5.3s 全部完整），这里只需要稳定地断言调用序列。
"""

from __future__ import annotations

import sys
import threading
import time
import types

import pytest


class _FakeEngine:
    """记录调用序列的假引擎，模拟「播一条要花一点时间」。"""

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def setProperty(self, name, value):
        self._calls.append(f"setProperty:{name}={value}")

    def say(self, text):
        self._calls.append(f"say:{text}")

    def startLoop(self):
        time.sleep(0.01)
        self._calls.append("startLoop")

    def endLoop(self):
        self._calls.append("endLoop")


@pytest.fixture
def fake_tts(monkeypatch):
    """把 pyttsx3 换成假实现，并返回 (get_notifier, calls)。"""
    calls: list[str] = []
    module = types.ModuleType("pyttsx3")
    module.init = lambda: _FakeEngine(calls)
    monkeypatch.setitem(sys.modules, "pyttsx3", module)

    from fire_sight.alerting import tts as tts_mod

    made: list = []

    def _factory(enabled: bool = True, rate: int = 160):
        notifier = tts_mod.TtsNotifier(enabled=enabled, rate=rate)
        made.append(notifier)
        return notifier

    yield _factory, calls
    for n in made:
        n.close()


def _wait_until(pred, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_every_alert_is_spoken_not_only_the_first(fake_tts):
    """每一条告警都要真的播出去——这是「只播第一次」的回归守卫。

    过去实现逐条 say()+runAndWait()：runAndWait 会先往队列塞一个 endLoop，
    它在下一条的开头被 pump 出去，于是 driver.stop() 调 `Speak("", 3)`
    （PURGEBEFORESPEAK）把正在播的那句掐掉。第一条之外的每一句都被静默吞掉。
    """
    factory, calls = fake_tts
    notifier = factory(enabled=True)

    for i in range(3):
        assert notifier.speak(f"第{i + 1}次明火告警") is True

    def three_spoken() -> bool:
        return (
            sum(1 for c in calls if c.startswith("say:")) >= 3
            and calls.count("startLoop") >= 3
            and calls.count("endLoop") >= 3
        )

    assert _wait_until(three_spoken), f"三条都应播报，实际序列={calls}"

    # 每条必须走完 say → startLoop → endLoop；缺 endLoop 会让下一次
    # startLoop 抛 "run loop already started"，缺 startLoop 则没人 pump。
    spoken = [c for c in calls if c.startswith("say:")]
    assert len(spoken) == 3
    for i, text in enumerate(spoken):
        tail = calls[calls.index(text) + 1 : calls.index(text) + 3]
        assert tail == ["startLoop", "endLoop"], f"第{i + 1}条循环不完整：{calls}"


def test_speaker_thread_survives_a_failing_utterance(fake_tts, monkeypatch):
    """单条播报抛异常不能让播报线程死掉。

    线程一死，speak() 还会继续返回 True（它只管入队），于是表现成
    「日志说播了、实际一声不吭」，比直接报错更难查。
    """
    factory, calls = fake_tts

    class _FlakyEngine(_FakeEngine):
        def startLoop(self):
            self._calls.append("startLoop")
            if len([c for c in self._calls if c.startswith("say:")]) == 1:
                raise RuntimeError("boom")

    module = types.ModuleType("pyttsx3")
    module.init = lambda: _FlakyEngine(calls)
    monkeypatch.setitem(sys.modules, "pyttsx3", module)

    notifier = factory(enabled=True)
    notifier.speak("第一条会炸")
    assert _wait_until(lambda: notifier._speaker is not None and
                       notifier._speaker.is_alive())
    assert notifier.speak("第二条要能播") is True
    assert _wait_until(
        lambda: "say:第二条要能播" in calls
    ), f"线程应在第一条失败后继续工作，实际={calls}"


def test_pause_silences_broadcast_and_resume_brings_it_back(fake_tts):
    """人工暂停：暂停期间不播报，恢复后立刻恢复。"""
    factory, calls = fake_tts
    notifier = factory(enabled=True)

    assert notifier.paused is False
    assert notifier.pause() is True
    assert notifier.paused is True
    # 幂等：重复调用不该改变状态，也不该报错
    assert notifier.pause() is False

    assert notifier.speak("暂停期间的告警") is False
    time.sleep(0.2)
    assert not [c for c in calls if c.startswith("say:")], "暂停期间不应发声"

    assert notifier.resume() is True
    assert notifier.paused is False
    assert notifier.resume() is False

    assert notifier.speak("恢复后的告警") is True
    assert _wait_until(lambda: "say:恢复后的告警" in calls), calls


def test_pause_drops_already_queued_utterances(fake_tts):
    """暂停要把已排队的倒掉，恢复后不该连环补播。

    队列里是「暂停前就来不及播」的旧告警；值班的人按下暂停就是全都确认过了，
    恢复时再补播一串等于二次骚扰。
    """
    factory, calls = fake_tts
    notifier = factory(enabled=True)

    # 先让线程卡在播报里，塞几条进队列
    blocker = threading.Event()
    original = _FakeEngine.startLoop

    def _slow_startloop(self):
        blocker.wait(timeout=5)
        original(self)

    _FakeEngine.startLoop = _slow_startloop
    try:
        notifier.speak("正在播")
        _wait_until(lambda: "say:正在播" in calls)
        for i in range(3):
            notifier.speak(f"排队{i}")
        assert _wait_until(lambda: notifier._queue.qsize() >= 3)
        notifier.pause()
        assert notifier._queue.empty(), "暂停应排空待播队列"
    finally:
        _FakeEngine.startLoop = original
        blocker.set()


def test_queue_is_bounded_so_repeat_fire_cannot_stall_broadcast(fake_tts):
    """持续明火时队列必须有上限。

    告警每 30 秒冷却一次、播报一条要好几秒，长时间跑会越积越多，
    播报永远落后于现场（「现在播的是十分钟前的事」）。丢最旧的、保最新的。
    """
    factory, _calls = fake_tts
    notifier = factory(enabled=True)

    for i in range(notifier._max_queue + 20):
        notifier.speak(f"洪水{i}")

    assert notifier._queue.qsize() <= notifier._max_queue
