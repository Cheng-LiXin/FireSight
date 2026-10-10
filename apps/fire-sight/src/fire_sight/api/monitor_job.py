"""RTSP 常驻监控：一条长跑的推理流水线 + 一帧最新画面供 MJPEG 推流。

为什么不另起一条流水线：`run_inference` 的读帧循环对 RTSP 就是无限流，天然常驻，
多帧确认 / 冷却 / 事件库 / 片段 / TTS 全都现成。缺的只是「从 Web 启停」与
「把标注帧送出去」——所以这里只做编排，不重写推理。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from fire_sight.api import deps
from fire_sight_platform.inference.service import InferConfig, run_inference

logger = logging.getLogger(__name__)

# 掉线后的退避：1s 起步、翻倍、封顶 30s。RTSP 掉线是常态不是事故，
# 但也不能每秒狂重连把编码器和日志刷爆。
BACKOFF_START_SEC = 1.0
BACKOFF_MAX_SEC = 30.0

# 状态机：
#   idle       没在跑
#   connecting 正在建流（含断线后重连中）
#   running    读到帧了
#   stopping   已收到停止，正在收流
#   stopped    用户主动停止
#   ended      源正常结束（视频/图片目录读完；网络流不算，那种是掉线）
#   failed     起不来或反复失败
ACTIVE = frozenset({"connecting", "running", "stopping"})


def is_stream_source(source: str) -> bool:
    """网络流地址。对它们来说「读完了」意味着掉线，要重连而不是收工。"""
    return str(source).lower().startswith(("rtsp://", "rtmp://", "http://", "https://"))


class MonitorManager:
    """单路常驻监控（单机单用户：同时只允许一路）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state: Dict[str, Any] = self._blank()
        # 最新标注帧（JPEG 字节）。刻意不进 _state：status() 要整份 JSON 序列化，
        # 带上二进制帧会把响应体撑爆还编不出来。
        self._jpeg: Optional[bytes] = None

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    @staticmethod
    def _blank() -> Dict[str, Any]:
        return {
            "status": "idle",
            "source": "",
            "camera_name": "",
            "started_at": None,
            "finished_at": None,
            "frames": 0,
            "alerts": 0,
            "reconnects": 0,
            "error": None,
        }

    def status(self) -> Dict[str, Any]:
        with self._lock:
            state = dict(self._state)
        # 播报暂停状态不存进 _state：它真正的家在进程级 TTS 单例上，存一份
        # 副本必然会出现两边不一致（暂停后监控重启，副本还是旧值）。
        # 这里每次现读，status 与 set_tts_paused 永远同源。
        state["tts_paused"] = self.tts_paused()
        return state

    def is_active(self) -> bool:
        return self.status()["status"] in ACTIVE

    def wait_until(self, pred: Callable[[Dict[str, Any]], bool], timeout: float = 5.0) -> bool:
        """轮询等待状态满足条件。给测试与「停完再起」用。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pred(self.status()):
                return True
            time.sleep(0.02)
        return pred(self.status())

    def wait(self, timeout: float = 5.0) -> bool:
        """等收流结束（退出 ACTIVE）。"""
        return self.wait_until(lambda s: s["status"] not in ACTIVE, timeout=timeout)

    # ------------------------------------------------------------------
    # 最新画面（MJPEG 消费）
    # ------------------------------------------------------------------

    def latest_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._jpeg

    def _on_frame(self, annotated: Any) -> None:
        import cv2

        ok, buf = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return
        with self._lock:
            self._state["frames"] = int(self._state.get("frames") or 0) + 1
            self._jpeg = buf.tobytes()
            # 只把「连接中」抬成「监控中」。stopping 不许被帧改回去——
            # 否则点了停止还显示在跑，看起来像停止没生效。
            if self._state["status"] == "connecting":
                self._state["status"] = "running"

    def _set(self, **kw: Any) -> None:
        with self._lock:
            self._state.update(kw)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(
        self,
        source: str,
        camera_name: str = "",
        device: str = "0",
        conf: float = 0.25,
        iou: float = 0.6,
        enable_tts: bool = True,
        clip_before_sec: float = 30.0,
        clip_after_sec: float = 30.0,
        weights: str = "",
    ) -> Dict[str, Any]:
        """启动常驻监控。GPU 被占时抛 GpuBusy，由路由转 409。"""
        with self._lock:
            if self._state["status"] in ACTIVE:
                raise deps.GpuBusy(deps.OWNER_MONITOR)

        # 先拿锁再起线程：拿不到就根本不该起，也不要留半个线程
        if not deps.GPU_LOCK.acquire(deps.OWNER_MONITOR):
            raise deps.GpuBusy(deps.GPU_LOCK.holder)

        try:
            run = deps.resolve_run(weights or None)
            self._stop = threading.Event()
            self._jpeg = None
            self._state = {
                **self._blank(),
                "status": "connecting",
                "source": source,
                "camera_name": camera_name,
                "started_at": datetime.now().isoformat(timespec="seconds"),
            }
            cfg = InferConfig(
                source=source,
                weights=str(run.best_pt),
                conf=conf,
                iou=iou,
                device=device,
                camera_name=camera_name,
                clip_before_sec=clip_before_sec,
                clip_after_sec=clip_after_sec,
                enable_tts=enable_tts,
                events_db=str(deps.EVENTS_DB),
            )
            self._thread = threading.Thread(
                target=self._run, args=(cfg,), name="monitor-infer", daemon=True
            )
            self._thread.start()
        except FileNotFoundError as exc:
            deps.GPU_LOCK.release(deps.OWNER_MONITOR)
            raise ValueError(str(exc)) from exc
        except Exception:
            # 拿到锁之后到线程起来之前的任何一步失败（线程数耗尽等），
            # 都要把锁还回去——否则显存被一个永远不起的监控占着，直到重启服务。
            # 状态也要挪出 ACTIVE：留着 connecting 会让下次 start 直接被判「已占用」。
            deps.GPU_LOCK.release(deps.OWNER_MONITOR)
            with self._lock:
                if self._state["status"] == "connecting":
                    self._state = self._blank()
            raise
        return self.status()

    def stop(self) -> bool:
        """请求停止。立刻把状态挪出 running，避免「点了停止还显示占用」。

        这里**不**等收流结束：`stopping` 必须立刻可见，否则「停完再起」会撞上
        ACTIVE 判定被拒（接口回 409「显存正被 monitor 占用」）。等终态这件事交给
        调用方（接口层用 wait_stopped），stop 本身因此永远是快的。
        """
        with self._lock:
            if self._state["status"] not in ACTIVE:
                return False
            if self._state["status"] != "stopping":
                self._state["status"] = "stopping"
        self._stop.set()
        return True

    def wait_stopped(self, timeout: float = 5.0) -> bool:
        """等到离开 stopping 落进终态。

        给接口层用：收流是异步结束的，但调用方需要一个「等一下就准」的答案。
        前端轮询间隔 2s，用户要多等两个来回才看到状态变化——这里直接在请求内等，
        通常几十毫秒就等到；等不到也不报错（后台仍在收尾，锁照常归还、状态照常
        落终态），所以 timeout 只是「尽快给准信」的耐心值，不是停止的硬期限。
        """
        return self.wait_until(lambda s: s["status"] != "stopping", timeout=timeout)

    # ------------------------------------------------------------------
    # 语音播报：人工暂停 / 恢复
    # ------------------------------------------------------------------
    def tts_paused(self) -> bool:
        """当前是否处于人工暂停播报。

        暂停状态挂在进程级 TTS 单例上（那儿才是真正发声的地方），所以这里
        只是读回来给界面显示——暂停不该影响告警入库和片段留存。
        """
        from fire_sight.alerting import get_tts

        return bool(get_tts(enabled=True).paused)

    def set_tts_paused(self, paused: bool) -> bool:
        """暂停/恢复播报。返回暂停后的状态。

        为什么要有这个开关：告警冷却 30 秒一条，火一直没灭就会一直播。
        值班的人需要「我现在去现场确认，别再喊了」的办法——冷却是机器决定的，
        不该由人反复去调参数。
        """
        from fire_sight.alerting import get_tts

        tts = get_tts(enabled=True)
        if paused:
            tts.pause()
        else:
            tts.resume()
        return bool(tts.paused)

    def stop_for_training(self, timeout: float = 10.0) -> bool:
        """训练优先：停掉监控并等它把**锁**真正还回来。

        只等状态是不够的：收尾线程把状态置成 stopped 之后、`_release_lock()` 之前
        还有一两句在跑，这段窗口里锁还攥在 monitor 手上。届时训练去 `acquire`
        非阻塞会拿到 False，用户看到的是一句莫名其妙的「GPU 正被 monitor 占用」
        ——而监控其实已经停了，再点一次就好。等锁空出来就能消掉这个假 409。
        """
        if not self.is_active():
            return False
        self.stop()
        self.wait(timeout=timeout)
        if self.is_active():
            raise deps.GpuBusy("monitor（正在停止，请稍候重试）")

        deadline = time.time() + timeout
        while deps.GPU_LOCK.holder == deps.OWNER_MONITOR and time.time() < deadline:
            time.sleep(0.01)
        if deps.GPU_LOCK.holder == deps.OWNER_MONITOR:
            raise deps.GpuBusy("monitor（正在停止，请稍候重试）")
        return True

    # ------------------------------------------------------------------
    # 后台线程
    # ------------------------------------------------------------------

    def _run(self, cfg: InferConfig) -> None:
        backoff = BACKOFF_START_SEC
        try:
            while not self._stop.is_set():
                self._set(status="connecting" if self._state["frames"] else self._state["status"])
                try:
                    stats = run_inference(cfg, frame_sink=self._on_frame, stop_event=self._stop)
                    # 轮次结束才有准确告警数；重连后要累加，不是覆盖——
                    # 界面上「告警 0」会让人以为监控没在做事。
                    self._set(
                        alerts=int(self._state.get("alerts") or 0) + int(getattr(stats, "alerts", 0) or 0)
                    )
                except Exception as exc:  # noqa: BLE001 - 单轮失败不该让线程死掉
                    logger.warning(f"监控推理轮次异常: {exc}")
                    self._set(error=str(exc))
                    if self._stop.is_set():
                        break
                    if not is_stream_source(cfg.source):
                        # 本地视频/目录读不了就是配置错了，重试一万次也一样
                        self._set(status="failed")
                        return
                    self._sleep_backoff(backoff)
                    backoff = min(backoff * 2, BACKOFF_MAX_SEC)
                    self._set(reconnects=int(self._state.get("reconnects") or 0) + 1)
                    continue

                if self._stop.is_set():
                    break

                if is_stream_source(cfg.source):
                    # 网络流「读完了」= 掉线：退避重连，不静默收工
                    self._set(reconnects=int(self._state.get("reconnects") or 0) + 1)
                    self._sleep_backoff(backoff)
                    backoff = min(backoff * 2, BACKOFF_MAX_SEC)
                    continue

                # 本地视频/图片目录读完 = 本轮结束
                self._set(status="ended")
                return

            self._set(status="stopped")
        finally:
            self._set(finished_at=datetime.now().isoformat(timespec="seconds"))
            self._release_lock()

    def _sleep_backoff(self, seconds: float) -> None:
        """可被停止信号打断的等待——停机不该卡在退避睡眠里。"""
        self._stop.wait(timeout=seconds)

    def _release_lock(self) -> None:
        try:
            # 带上持有者身份：收尾线程迟到时不至于放掉别人刚拿到的锁
            deps.GPU_LOCK.release(deps.OWNER_MONITOR)
        except Exception as exc:  # noqa: BLE001 - 锁状态异常只记日志，别盖掉真实错误
            logger.warning(f"释放 GPU 锁失败: {exc}")


# 进程内单例：路由、训练任务抢锁都走同一个实例
MONITOR = MonitorManager()
