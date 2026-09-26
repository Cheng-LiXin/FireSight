"""离线中文 TTS 通知(可禁用)。"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class TtsNotifier:
    """pyttsx3 包装；无环境/无库时优雅降级为日志。"""

    def __init__(self, enabled: bool = True, rate: int = 160) -> None:
        self.enabled = enabled
        self.rate = rate
        self._engine = None
        self._lock = threading.Lock()
        if enabled:
            try:
                import pyttsx3

                self._engine = pyttsx3.init()
                self._engine.setProperty("rate", rate)
            except Exception as exc:
                logger.warning(f"TTS 不可用，降级为日志: {exc}")
                self._engine = None

    def speak(self, text: str) -> bool:
        if not self.enabled:
            return False
        if self._engine is None:
            logger.info(f"[TTS] {text}")
            return False
        with self._lock:
            try:
                self._engine.say(text)
                self._engine.runAndWait()
                return True
            except Exception as exc:
                logger.warning(f"TTS 播报失败: {exc}")
                return False
