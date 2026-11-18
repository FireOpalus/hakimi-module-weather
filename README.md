# Hakimi · 天气观测

对话 AI、世界引擎、其他模块及管理后台共用一个按需读取接口。启动、整点和后台状态刷新均不会发起天气请求。

在控制台的「功能模块 → 天气观测 → 观测地点」选择城市、输入经纬度，或点击「识别服务器地点」预览服务器出口 IP 对应的城市。点「保存地点」后，可以点击「查询当前天气」读取新地点的数据。模块关闭时也能配置地点。后台选择加密保存在 SQLite 中，恢复环境默认后使用 `HAKIMI_WEATHER_LATITUDE` 和 `HAKIMI_WEATHER_LONGITUDE`。

在模块内调用 `result = await service.life_engine.query("weather.read")`。当 `result.status == "done"` 时，`result.details` 包含 `observation`、可读的 `summary` 和 `source`。后台通过受鉴权的 `GET /admin/api/life/modules/weather/current` 取得同样的数据。观测时间是 Unix 秒，气温为 °C，降水为采样区间的毫米数，短波辐射为 W/m²。相同地点的并发查询共用有效观测，超过上游给出的区间后由下次调用更新。

遇到连接超时，服务会向 Google 公共 DNS 查询 Open-Meteo 的域名，再向同一官方域名的一个公开节点重试一次。地点只发送给天气服务；备用连接同样校验原域名的 HTTPS 证书。

城市搜索使用 Open-Meteo，地名数据由 GeoNames 提供。IP 定位只在管理员请求时执行，识别结果可能与实际城市有偏差。公开天气事件只包含观测值。[Open-Meteo Geocoding](https://open-meteo.com/en/docs/geocoding-api) · [GeoNames](https://www.geonames.org/) · [IPWhois](https://ipwhois.io/documentation)

模块在宿主中的目录为 `src/hakimi/modules/platforms/weather/`，Python 入口为 `hakimi.modules.weather`。共用接口和四个模块的固定版本见[模块目录](https://github.com/FireOpalus/hakimi-modules/tree/main/platforms)。

在[主仓](https://github.com/FireOpalus/hakimi-surf)执行 `git submodule update --init --recursive` 后，可用 `.venv/bin/pytest -q` 运行集成测试。发布时先推送本仓库提交，再更新模块目录的对应指针，最后更新主仓的目录指针。
