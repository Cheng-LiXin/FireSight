# frame_source —— 可插拔帧输入源

统一 `for frame in source` 接口，支持：

| 源 | 示例 |
|---|---|
| 摄像头 | `"0"` |
| 视频文件 | `"clip.mp4"` |
| 图片文件夹 | `"./frames"` |
| RTSP 流 | `"rtsp://..."` |

加源：`@register_source` 一行注册，不改本模块。
S3 实装；骨架已对齐参考项目 `odplatform/frame_source`。
