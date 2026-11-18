# Hakimi · 天气观测

整点天气观测、天气事件和可选的每日提醒。

在控制台的「功能模块 → 天气观测 → 观测地点」选择城市、输入经纬度，或点击「识别服务器地点」预览服务器出口 IP 对应的城市。点「保存地点」后，小时采样和每日提醒使用同一地点。更换地点后等待新地点的首次采样。模块关闭时也能配置地点。后台选择加密保存在 SQLite 中，恢复环境默认后使用 `HAKIMI_WEATHER_LATITUDE` 和 `HAKIMI_WEATHER_LONGITUDE`。

城市搜索使用 Open-Meteo，地名数据由 GeoNames 提供。IP 定位只在管理员请求时执行，识别结果可能与实际城市有偏差。公开天气事件只包含观测值。[Open-Meteo Geocoding](https://open-meteo.com/en/docs/geocoding-api) · [GeoNames](https://www.geonames.org/) · [IPWhois](https://ipwhois.io/documentation)

模块在宿主中的目录为 `src/hakimi/modules/platforms/weather/`，Python 入口为 `hakimi.modules.weather`。共用接口和四个模块的固定版本见[模块目录](https://github.com/FireOpalus/hakimi-modules/tree/main/platforms)。

在[主仓](https://github.com/FireOpalus/hakimi-surf)执行 `git submodule update --init --recursive` 后，可用 `.venv/bin/pytest -q` 运行集成测试。发布时先推送本仓库提交，再更新模块目录的对应指针，最后更新主仓的目录指针。
