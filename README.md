# Hakimi · 天气观测

整点天气观测、天气事件和可选的每日提醒。

模块在宿主中的目录为 `src/hakimi/modules/platforms/weather/`，Python 入口为 `hakimi.modules.weather`。共用接口和四个模块的固定版本见[模块目录](https://github.com/FireOpalus/hakimi-modules/tree/main/platforms)。

在[主仓](https://github.com/FireOpalus/hakimi-surf)执行 `git submodule update --init --recursive` 后，可用 `.venv/bin/pytest -q` 运行集成测试。发布时先推送本仓库提交，再更新模块目录的对应指针，最后更新主仓的目录指针。
