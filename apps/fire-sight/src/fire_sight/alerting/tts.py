"""离线中文 TTS 通知(可禁用)。

设计约束（来自真实踩坑，勿回退）:
- 引擎与播报线程**全进程只有一份**。pyttsx3.init() 会 new 一个 sapi5 driver，
  driver 持有 COM 的 SAPI.SPVoice 对象；每次重连都 new 一个等于每轮泄漏一个
  COM 句柄，而且 SAPI 只会用最先注册的声音源，后面那些既占资源又发不出声。
- 播报**不占用调用方线程**。runAndWait() 是阻塞的，早期实现在推理主线程里直接
  调它，结果一条告警的播报时长（数秒）里读帧/推理/推流全停，监控看着像卡死。
  现在 say + 播完整条全部搬到专用播报线程，调用方只做入队，立即返回。
- 单例而非「引擎缓存」是有原因的：pyttsx3 的事件循环状态挂在 Engine 上，
  两个 notifier 共用一个引擎、各自起线程就会互相踩这个状态。所以共享粒度
  必须大到「一个进程一个引擎 + 一个播报线程」。
- 播报线程是 daemon 且只创建一次（enabled 的那一支），close() 后可重新 get。

**为什么不用 runAndWait()（这是「只播第一次」的根因，别改回去）**:
  逐条 say() + runAndWait() 会让第一声之外的全部被掐断。实测（探针 A）：
  第 1 条播 5.79s 完整，第 2、3 条各 0.02s 且 `completed=False`。
  链条是（pyttsx3 的 driver.py + sapi5.py）:
    1. DriverProxy.__init__ 里 `self._busy = True`。
    2. Engine.runAndWait() 先 `push(engine.endLoop)`，再调 driver.startLoop()；
       startLoop 首轮 `setBusy(False)` 才开始 pump 队列。
    3. 于是**第 1 条**：say() 时 _busy 仍 True，命令只入队；startLoop 首轮才把它
       pump 出去（真正出声），播完 EndStream → d.endLoop() → 退出循环 → 播完。
    4. **第 2 条**：上一轮 runAndWait 收尾做了 `proxy.setBusy(False)`，_busy=False。
       所以 say() 的 push 当场被 pump（真出声），紧接着 startLoop 首轮
       setBusy(False) 又把那个早排好的 endLoop pump 出去 → proxy.endLoop →
       driver.stop() → `Speak("", 3)`（SVSFlagsAsync|SVSFPurgeBeforeSpeak）
       **把正在播的这一句 Purge 掉**。
  所以这里改用 say() + startLoop() + endLoop()：startLoop 不 push endLoop，
  循环退出交给 EndStream 回调里的 driver.endLoop()，播完整条；随后显式 endLoop()
  复位 Engine._inLoop（否则下次 startLoop 会抛 "run loop already started"）。
  实测（探针 B）三条各播 5.31/5.30/5.45s，全部完整。

线程模型（COM 约束）:
  comtypes 的 SAPI 对象是 STA（单线程单元），EndStream/EndInput 回调只在**创建
  该 COM 对象的那个线程**的消息队列里派发。所以引擎必须在播报线程内 init，
  且只能由该线程 PumpWaitingMessages —— 别的线程碰它就是静默无回调。
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class TtsNotifier:
    """pyttsx3 包装；无环境/无库时优雅降级为日志。

    线程模型：对外接口（speak/close/pause/resume）线程安全；引擎只在
    _speaker 线程里创建与使用，任何其他线程都不得触碰它。speak() 永不阻塞调用方。

    播报暂停：pause() 之后 speak() 直接拒收（返回 False，不入队），已排队的由
    _drain_queue() 排空，播报线程遇到 paused 的条目也直接跳过。三个动作都指向
    同一件事——恢复的瞬间不该把暂停期间的告警一次性倾泻出来。
    """

    def __init__(self, enabled: bool = True, rate: int = 160) -> None:
        self.enabled = enabled
        self.rate = rate
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._lock = threading.Lock()
        self._closed = False
        # 只表示「降级为日志」：播报线程起不来或引擎建不出来时置 True。
        self._degraded = False
        self._speaker: Optional[threading.Thread] = None
        # 人工暂停：事件入库、片段留存照常，只有语音被掐掉。
        self._paused = False
        # 队列上限：持续明火时冷却期会不断产生新告警，无上限队列会让播报
        # 永远落后于真实时间（「现在播的是三分钟前的事」）。丢最旧的，保留最新的。
        self._max_queue = 32
        if enabled:
            self._start_speaker()

    # ------------------------------------------------------------------
    # 播报线程：整个进程只会有一个在跑
    # ------------------------------------------------------------------
    def _start_speaker(self) -> None:
        def _loop() -> None:
            try:
                import pyttsx3

                engine = pyttsx3.init()
                engine.setProperty("rate", self.rate)
            except Exception as exc:  # noqa: BLE001 - 没装库/无声音源不拖垮监控
                logger.warning(f"TTS 不可用，降级为日志: {exc}")
                self._degraded = True
                return
            while True:
                text = self._queue.get()
                if text is None:  # 退出哨兵
                    return
                if self._paused:
                    # 人工暂停：排掉这一条，不发声。宁可少报也不要攒成
                    # 恢复后的「连环播报」。
                    continue
                try:
                    engine.say(text)
                    # startLoop 阻塞到本条 EndStream 回调触发 driver.endLoop()。
                    # 不能换成 runAndWait()——见模块头部那段「为什么不用」。
                    engine.startLoop()
                    # startLoop 返回后 Engine._inLoop 仍是 True（真正清它的是
                    # driver 层），显式复位，否则下一条会抛 "run loop already started"。
                    engine.endLoop()
                except Exception as exc:  # noqa: BLE001 - 单次失败不退出线程
                    logger.warning(f"TTS 播报失败: {exc}")

        self._speaker = threading.Thread(target=_loop, name="tts-speaker", daemon=True)
        self._speaker.start()

    def speak(self, text: str) -> bool:
        """入队播报，立即返回。False = 本次没有真正发声（禁用/降级/已关/已暂停）。"""
        if not self.enabled:
            return False
        text = (text or "").strip()
        if not text:
            return False
        with self._lock:
            if self._closed:
                return False
            if self._degraded or self._speaker is None:
                # 降级路径与历史行为一致：把内容打进日志，不假装已播报。
                logger.info(f"[TTS] {text}")
                return False
            if self._paused:
                # 暂停期间不排队：否则恢复瞬间会把积压的告警一次性全放出来。
                logger.info(f"[TTS 已暂停] {text}")
                return False
            if self._queue.qsize() >= self._max_queue:
                # 丢最旧的一条，保住最新告警能及时播到。
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    pass
            self._queue.put(text)
        return True

    # ------------------------------------------------------------------
    # 人工暂停 / 恢复
    # ------------------------------------------------------------------
    def pause(self) -> bool:
        """暂停播报。返回 True = 本次状态发生了变化。"""
        with self._lock:
            if self._closed or self._paused:
                return False
            self._paused = True
        self._drain_queue()
        return True

    def resume(self) -> bool:
        """恢复播报。返回 True = 本次状态发生了变化。"""
        with self._lock:
            if self._closed or not self._paused:
                return False
            self._paused = False
        return True

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def _drain_queue(self) -> None:
        """排空待播队列。

        暂停时把已排队的都丢掉：它们都是「暂停前还没来得及播」的旧告警，
        值班的人已经按暂停的意思确认过了，恢复后重播只会造成二次骚扰。
        """
        dropped: list[str] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:  # 别把退出哨兵吃掉
                self._queue.put(None)
                break
            dropped.append(item)
        if dropped:
            logger.info(f"[TTS] 暂停排空 {len(dropped)} 条待播")

    def close(self) -> None:
        """停播报线程；幂等，且不会无限等 SAPI。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._speaker
            self._speaker = None
        if thread is not None and thread.is_alive():
            self._queue.put(None)
            # SAPI 偶尔会卡；停止链路不能因此再卡一次。
            thread.join(timeout=2.0)


# ----------------------------------------------------------------------
# 进程级单例
# ----------------------------------------------------------------------
_singleton: Optional[TtsNotifier] = None
_singleton_lock = threading.Lock()


def get_tts(enabled: bool = True, rate: int = 160) -> TtsNotifier:
    """取全进程唯一的 TTS 通知器。

    enabled / rate 只在第一次调用时生效（之后复用）；换参数请先 close_tts()。
    """
    global _singleton
    if not enabled:
        return TtsNotifier(enabled=False, rate=rate)
    with _singleton_lock:
        if _singleton is None or _singleton._closed:
            _singleton = TtsNotifier(enabled=True, rate=rate)
        return _singleton


def close_tts() -> None:
    """关掉进程级通知器（测试/退出用）。"""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.close()
            _singleton = None


__all__ = ["TtsNotifier", "get_tts", "close_tts"]
