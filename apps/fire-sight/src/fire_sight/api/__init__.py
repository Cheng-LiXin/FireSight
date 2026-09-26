"""FireSight REST 层。

分层：routers（HTTP 契约） -> services（数据服务） -> 引擎（platform / scene）。
路由只做参数校验与序列化，不直接碰 SQL 或 YOLO。

入口：``fire_sight.api.app:app``（fs-serve）。此处不 re-export，避免循环导入。
"""
