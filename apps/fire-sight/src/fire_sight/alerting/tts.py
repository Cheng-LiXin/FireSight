"""离线中文 TTS 通知(可禁用)。

设计约束（来自真实踩坑，勿回退）:
- 引擎与播报线程**全进程只有一份**。pyttsx3.init() 会 new 一个 sapi5 driver，
  driver 持有 COM 的 SAPI.SPVoice 对象；每次重连都 new 一个等于每轮泄漏一个
  COM 句柄，而且 SAPI 只会用最先注册的声音源，后面那些既占资源又发不出声。
- 播报**不占用调用方线程**。runAndWait() 是阻塞的，早期实现在推理主线程里直接
  调它，结果一条告警的播报时长（数秒）里读帧/推理/推流全停，监控看着像卡死。
  现在 say + runAndWait 整体搬到专用播报线程，调用方只做入队，立即返回。
- 单例而非「引擎缓存」是有原因的：pyttsx3 的 Engine.runAndWait() 在自己已经
  处于事件循环时会抛 RuntimeError("run loop already started")。两个 notifier
  共用一个引擎、各自起线程，就会并发跑 runAndWait 而撞上这条。所以共享粒度
  必须大到「一个进程一个引擎 + 一个播报线程」。
- 播报线程是 daemon 且只创建一次（enabled 的那一支），close() 后可重新 get。
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class TtsNotifier:
    """pyttsx3 包装；无环境/无库时优雅降级为日志。

    线程模型：对外接口（speak/close）线程安全；引擎只在 _speaker 线程里创建与
    使用，任何其他线程都不得触碰它。speak() 永不阻塞调用方。
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
                try:
                    engine.say(text)
                    engine.runAndWait()
                except Exception as exc:  # noqa: BLE001 - 单次失败不退出线程
                    logger.warning(f"TTS 播报失败: {exc}")

        self._speaker = threading.Thread(target=_loop, name="tts-speaker", daemon=True)
        self._speaker.start()

    def speak(self, text: str) -> bool:
        """入队播报，立即返回。False = 本次没有真正发声（禁用/降级/已关）。"""
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
            self._queue.put(text)
        return True

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
