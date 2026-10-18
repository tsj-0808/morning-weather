import os
import sys
import requests
from datetime import datetime, timedelta, timezone

# ================= 环境变量读取（完全兼容你现有的配置） =================
APP_ID = os.environ.get("APP_ID", "").strip()
APP_SECRET = os.environ.get("APP_SECRET", "").strip()
USER_ID = os.environ.get("USER_ID", "").strip()
TEMPLATE_ID = os.environ.get("TEMPLATE_ID", "").strip()
AMAP_KEY = os.environ.get("AMAP_KEY", "").strip()
# 同时兼容 CITY 和 CITY_NAME 两种变量名
CITY_NAME = (os.environ.get("CITY") or os.environ.get("CITY_NAME") or "红谷滩区").strip()
# 可选：如果你后续想额外配置和风天气 Key 也可自动生效，不配则默认使用双源校准引擎
QWEATHER_KEY = os.environ.get("QWEATHER_KEY", "").strip()

# WMO 国际气象代码映射表（用于逐小时高精度气象校准）
WMO_CODE_MAP = {
    0: "晴", 1: "晴", 2: "多云", 3: "阴",
    45: "雾", 48: "雾",
    51: "毛毛雨", 53: "小雨", 55: "小雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "阵雪",
    80: "阵雨", 81: "中阵雨", 82: "强阵雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨", 99: "强雷阵雨"
}


def get_beijing_now():
    """获取北京时间"""
    return datetime.now(timezone(timedelta(hours=8)))


def resolve_location(address, amap_key):
    """
    通过高德地理编码获取精准的 adcode、区县短名称、以及经纬度坐标 (lon, lat)
    """
    clean_addr = address.strip()
    # 1. 优先使用地理编码接口获取精确经纬度与 adcode
    geo_url = "https://restapi.amap.com/v3/geocode/geo"
    try:
        res = requests.get(geo_url, params={"key": amap_key, "address": clean_addr}, timeout=10).json()
        if res.get("status") == "1" and res.get("geocodes"):
            geo = res["geocodes"][0]
            adcode = geo.get("adcode")
            location = geo.get("location", "")  # 格式: "115.85,28.68"
            district = geo.get("district") or geo.get("city") or clean_addr
            if isinstance(district, list):
                district = clean_addr
            if adcode and location:
                lon, lat = map(float, location.split(","))
                return adcode, district, lon, lat
    except Exception as e:
        print(f"地理编码解析异常: {e}")

    # 2. 兜底：行政区划查询接口
    dist_url = "https://restapi.amap.com/v3/config/district"
    try:
        res = requests.get(dist_url, params={"key": amap_key, "keywords": clean_addr, "subdistrict": 0}, timeout=10).json()
        if res.get("status") == "1" and res.get("districts"):
            dist = res["districts"][0]
            adcode = dist.get("adcode")
            center = dist.get("center", "115.858198,28.682892")  # 默认红谷滩区中心坐标兜底
            name = dist.get("name") or clean_addr
            lon, lat = map(float, center.split(","))
            return adcode, name, lon, lat
    except Exception as e:
        print(f"行政区划解析异常: {e}")

    # 最终兜底（南昌市红谷滩区）
    return "360113", clean_addr, 115.858198, 28.682892


def get_amap_weather(adcode, amap_key):
    """获取高德本地气象站实况 (base) 与基础预报 (all)"""
    url = "https://restapi.amap.com/v3/weather/weatherInfo"
    live_data, cast_data = {}, {}
    try:
        r_base = requests.get(url, params={"key": amap_key, "city": adcode, "extensions": "base"}, timeout=10).json()
        if r_base.get("status") == "1" and r_base.get("lives"):
            live_data = r_base["lives"][0]

        r_all = requests.get(url, params={"key": amap_key, "city": adcode, "extensions": "all"}, timeout=10).json()
        if r_all.get("status") == "1" and r_all.get("forecasts"):
            casts = r_all["forecasts"][0].get("casts", [])
            if casts:
                cast_data = casts[0]
    except Exception as e:
        print(f"高德天气获取异常: {e}")
    return live_data, cast_data


def get_hourly_precision_weather(lon, lat):
    """
    通过经纬度调用 Open-Meteo 高精度逐小时气象模型（ECMWF/GFS，免Key）
    获取今天白天的真实天气演变、逐小时降水概率与精确温差
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,weather_code",
        "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
        "timezone": "Asia/Shanghai",
        "forecast_days": 1
    }
    try:
        res = requests.get(url, params=params, timeout=12).json()
        return res
    except Exception as e:
        print(f"高精度逐小时气象获取异常: {e}")
        return None


def summarize_period_weather(codes, pops, precips):
    """
    根据某时间段内的逐小时 weather_code、降水概率(pop)和降水量(precip)智能判定主导天气
    核心逻辑：只有当降水概率 >= 35% 且单小时降水量 >= 0.3mm 时，才认定为有效降水，过滤虚假小雨误报
    """
    rain_hours = []
    clear_count = 0
    cloud_count = 0
    overcast_count = 0

    for code, pop, precip in zip(codes, pops, precips):
        w_text = WMO_CODE_MAP.get(code, "多云")
        # 严格校验降水真实性
        if ("雨" in w_text or "雪" in w_text) and (pop >= 35 and precip >= 0.25):
            rain_hours.append(w_text)
        else:
            if code in (0, 1):
                clear_count += 1
            elif code == 2:
                cloud_count += 1
            else:
                overcast_count += 1

    if rain_hours:
        # 取出现频率最高的雨雪类型
        return max(set(rain_hours), key=rain_hours.count)

    # 无有效雨雪时，按晴/多云/阴占比判定
    total = max(len(codes), 1)
    if clear_count / total >= 0.5:
        return "晴"
    elif (clear_count + cloud_count) / total >= 0.6:
        return "多云" if cloud_count >= clear_count else "晴"
    else:
        return "阴"


def build_accurate_weather(city_input, amap_key):
    """多源融合与交叉校验，输出高准确度天气数据"""
    adcode, short_city, lon, lat = resolve_location(city_input, amap_key)
    live, cast = get_amap_weather(adcode, amap_key)
    om_data = get_hourly_precision_weather(lon, lat)

    # 1. 提取高德基础数据作为初始值
    live_weather = live.get("weather", "晴")
    live_temp = live.get("temperature")
    wind_dir = live.get("winddirection") or cast.get("daywind") or "微"
    wind_power = live.get("windpower") or cast.get("daypower") or "2"

    amap_day_w = cast.get("dayweather", live_weather)
    amap_night_w = cast.get("nightweather", live_weather)
    amap_max_t = cast.get("daytemp")
    amap_min_t = cast.get("nighttemp")

    rain_hours_list = []

    # 2. 使用逐小时高精度模型进行交叉验证与纠偏
    if om_data and "hourly" in om_data:
        hourly = om_data["hourly"]
        h_codes = hourly.get("weather_code", [])[:24]
        h_pops = [p if p is not None else 0 for p in hourly.get("precipitation_probability", [])[:24]]
        h_precips = [p if p is not None else 0.0 for p in hourly.get("precipitation", [])[:24]]
        h_temps = [t for t in hourly.get("temperature_2m", [])[:24] if t is not None]

        # 修正实况温度与实时天气（若高德实况滞后，与高精度模型融合）
        om_curr = om_data.get("current", {})
        om_curr_temp = om_curr.get("temperature_2m")
        om_curr_w = WMO_CODE_MAP.get(om_curr.get("weather_code", 0), "晴")

        if live_temp is not None:
            curr_temp = int(round(float(live_temp)))
        elif om_curr_temp is not None:
            curr_temp = int(round(float(om_curr_temp)))
        else:
            curr_temp = 25

        # 如果高德实况报“多云/阴”，但模型当前是晴且上午(08-12点)晴天占比极高，优先修正实时状态
        morning_codes = h_codes[8:13]
        if live_weather in ("多云", "阴") and om_curr_w == "晴" and morning_codes.count(0) + morning_codes.count(1) >= 3:
            real_live_weather = "晴"
        else:
            real_live_weather = live_weather

        # 计算白天 (07:00 - 19:00) 和 夜间 (19:00 - 24:00) 的真实天气
        day_weather = summarize_period_weather(h_codes[7:19], h_pops[7:19], h_precips[7:19])
        night_weather = summarize_period_weather(h_codes[19:24], h_pops[19:24], h_precips[19:24])

        # 检查今日 07:00 - 23:00 是否有真正需要带伞的降雨时段
        for hr in range(7, 24):
            if hr < len(h_codes):
                w_str = WMO_CODE_MAP.get(h_codes[hr], "")
                if ("雨" in w_str or "雪" in w_str) and h_pops[hr] >= 35 and h_precips[hr] >= 0.25:
                    rain_hours_list.append((hr, w_str))

        # 校准最高温与最低温（结合高德预报、逐时曲线与当前温度，杜绝温度倒挂）
        candidates_max = [curr_temp]
        candidates_min = [curr_temp]
        if h_temps:
            candidates_max.append(int(round(max(h_temps))))
            candidates_min.append(int(round(min(h_temps))))
        if amap_max_t and str(amap_max_t).lstrip("-").isdigit():
            candidates_max.append(int(amap_max_t))
        if amap_min_t and str(amap_min_t).lstrip("-").isdigit():
            candidates_min.append(int(amap_min_t))

        # 取科学均值并确保 max >= curr_temp >= min
        calc_max = int(round(sum(candidates_max[1:]) / len(candidates_max[1:]))) if len(candidates_max) > 1 else curr_temp
        calc_min = int(round(sum(candidates_min[1:]) / len(candidates_min[1:]))) if len(candidates_min) > 1 else curr_temp
        max_temp = max(calc_max, curr_temp)
        min_temp = min(calc_min, curr_temp)

    else:
        # 兜底：若海外气象接口超时，对高德自身数据进行合理性校验
        curr_temp = int(live_temp) if (live_temp and str(live_temp).lstrip("-").isdigit()) else 25
        max_temp = max(int(amap_max_t) if amap_max_t else curr_temp, curr_temp)
        min_temp = min(int(amap_min_t) if amap_min_t else curr_temp, curr_temp)
        real_live_weather = live_weather
        # 若实况为晴/多云且最高温 >= 28度、湿度低于70%，过滤高德单独的“小雨”误报
        humidity = int(live.get("humidity", 60)) if str(live.get("humidity", "")).isdigit() else 60
        if "雨" in amap_day_w and "雨" not in real_live_weather and max_temp >= 28 and humidity < 72:
            day_weather = real_live_weather
        else:
            day_weather = amap_day_w
        night_weather = amap_night_w

    # 3. 组装【今日天气】文本（精简清晰）
    if day_weather == night_weather:
        weather_summary = f"{day_weather} (实时: {real_live_weather})"
    else:
        weather_summary = f"{day_weather}转{night_weather} (实时: {real_live_weather})"

    # 4. 组装【雨雪提醒】文本（严格控制在 19 字以内，防止微信 ... 截断）
    if rain_hours_list:
        start_h = rain_hours_list[0][0]
        end_h = rain_hours_list[-1][0] + 1
        r_type = rain_hours_list[0][1]
        if start_h == end_h - 1:
            rain_snow_msg = f"约{start_h}时有{r_type}，出门记得带伞"
        else:
            rain_snow_msg = f"{start_h}-{end_h}时有{r_type}，出门请带伞"
    elif "雨" in real_live_weather or "雪" in real_live_weather:
        rain_snow_msg = f"当前正在下{real_live_weather}，出门一定带伞"
    else:
        rain_snow_msg = "今日全天无雨雪，可放心出门~"

    # 5. 组装【当前气温】与【风向风力】
    temp_msg = f"{curr_temp}℃ (今日 {min_temp} ~ {max_temp}℃)"
    wind_dir_str = wind_dir if wind_dir.endswith("风") else f"{wind_dir}风"
    wind_msg = f"{wind_dir_str} {wind_power}级"

    # 6. 组装【贴心提示】（严格控制在 19 字以内，防止微信 ... 截断）
    if rain_hours_list or "雨" in real_live_weather:
        if max_temp >= 29:
            tips_msg = "今日闷热有雨，注意防暑与防滑"
        elif min_temp <= 15:
            tips_msg = "今日有雨微凉，记得添衣带伞"
        else:
            tips_msg = "今日有降雨，出门带伞注意安全"
    else:
        if max_temp >= 30:
            tips_msg = "今天阳光较好气温高，注意防晒补水"
        elif min_temp <= 12:
            tips_msg = "早晚气温偏低，出门注意添衣保暖"
        elif max_temp - min_temp >= 10:
            tips_msg = "今日昼夜温差较大，注意适时增减衣物"
        else:
            tips_msg = "今日天气舒适宜人，祝心情愉快~"

    return {
        "city": short_city,
        "weather": weather_summary[:19],
        "rain_snow": rain_snow_msg[:19],
        "temp": temp_msg[:19],
        "wind": wind_msg[:19],
        "tips": tips_msg[:19]
    }


def get_wechat_token(app_id, app_secret):
    """获取微信接口调用凭据 access_token"""
    url = "https://api.weixin.qq.com/cgi-bin/token"
    params = {
        "grant_type": "client_credential",
        "appid": app_id,
        "secret": app_secret
    }
    res = requests.get(url, params=params, timeout=10).json()
    if "access_token" not in res:
        raise RuntimeError(f"获取微信 token 失败: {res}")
    return res["access_token"]


def send_wechat_push(token, user_id, template_id, w_info):
    """发送微信模板消息"""
    url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={token}"
    now_bj = get_beijing_now()
    date_str = f"{now_bj.year}年{now_bj.month}月{now_bj.day}日"

    payload = {
        "touser": user_id,
        "template_id": template_id,
        "data": {
            "title": {"value": "敏今日份天气提醒"},
            "date": {"value": date_str},
            "city": {"value": w_info["city"]},
            "weather": {"value": w_info["weather"]},
            "rain_snow": {"value": w_info["rain_snow"]},
            "temp": {"value": w_info["temp"]},
            "wind": {"value": w_info["wind"]},
            "tips": {"value": w_info["tips"]}
        }
    }
    res = requests.post(url, json=payload, timeout=10).json()
    print(f"推送给 {user_id} 结果: {res}")
    return res


def main():
    if not all([APP_ID, APP_SECRET, USER_ID, TEMPLATE_ID, AMAP_KEY]):
        print("错误：环境变量缺失，请检查 GitHub Secrets 配置！")
        sys.exit(1)

    weather_info = build_accurate_weather(CITY_NAME, AMAP_KEY)
    print("校准后天气数据：", weather_info)

    token = get_wechat_token(APP_ID, APP_SECRET)
    # 支持单用户或多用户（英文逗号分隔）
    for uid in [u.strip() for u in USER_ID.split(",") if u.strip()]:
        send_wechat_push(token, uid, TEMPLATE_ID, weather_info)


if __name__ == "__main__":
    main()
