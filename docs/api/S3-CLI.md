# S3 CLI — fs-infer

fs-infer --source clip.mp4 --weights best.pt --no-tts --max-frames 50

参数: --source --weights --conf --iou --device --camera-name --confirm-frames
--cooldown --clip-before --clip-after --no-tts --save-dir --events-db --max-frames

- `--clip-before` / `--clip-after`：告警片段保留**告警前/后各多少秒**（默认各 30）。
  帧数按源的标称帧率换算（摄像头/视频取 `CAP_PROP_FPS`），图片目录这类没有帧率的按 25fps 折算。
- `--camera-name`：位置名，进事件与 TTS 播报文案（「检测到明火，位置：东门」）。
