import os
import sys
import json
import requests
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

# ================= 环境变量读取 =================
APP_ID = os.environ.get("APP_ID", "").strip()
APP_SECRET = os.environ.get("APP_SECRET", "").strip()
USER_ID = os.environ.get("USER_ID", "").strip()
TEMPLATE_ID = os.environ.get("TEMPLATE_ID", "").strip()
AMAP_KEY = os.environ.get("AMAP_KEY", "").strip()
CITY_NAME = (os.environ.get("CITY") or os.environ.get("CITY_NAME") or "红谷滩区").strip()

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
    return datetime.now(timezone(timedelta(hours=8)))


def post_json_utf8(url, data_dict):
    """强制使用 UTF-8 编码，防止手机端产生 \\ud83c 乱码"""
    headers = {"Content-Type": "application/json; charset=utf-8"}
    json_bytes = json.dumps(data_dict, ensure_ascii=False).encode("utf-8")
    return requests.post(url, data=json_bytes, headers=headers, timeout=10).json()


def resolve_location(address, amap_key):
    clean_addr = address.strip()
    geo_url = "https://restapi.amap.com/v3/geocode/geo"
    try:
        res = requests.get(geo_url, params={"key": amap_key, "address": clean_addr}, timeout=10).json()
        if res.get("status") == "1" and res.get("geocodes"):
            geo = res["geocodes"][0]
            adcode = geo.get("adcode")
            location = geo.get("location", "")
            district = geo.get("district") or geo.get("city") or clean_addr
            if isinstance(district, list):
                district = clean_addr
            if adcode and location:
                lon, lat = map(float, location.split(","))
                return adcode, district, lon, lat
    except Exception as e:
        print(f"地理编码解析异常: {e}")

    dist_url = "https://restapi.amap.com/v3/config/district"
    try:
        res = requests.get(dist_url, params={"key": amap_key, "keywords": clean_addr, "subdistrict": 0}, timeout=10).json()
        if res.get("status") == "1" and res.get("districts"):
            dist = res["districts"][0]
            adcode = dist.get("adcode")
            center = dist.get("center", "115.858198,28.682892")
            name = dist.get("name") or clean_addr
            lon, lat = map(float, center.split(","))
            return adcode, name, lon, lat
    except Exception as e:
        print(f"行政区划解析异常: {e}")

    return "360113", clean_addr, 115.858198, 28.682892


def get_amap_weather(adcode, amap_key):
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
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,weather_code",
        "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code",
        "timezone": "Asia/Shanghai",
        "forecast_days": 1
    }
    try:
        return requests.get(url, params=params, timeout=12).json()
    except Exception as e:
        print(f"高精度气象获取异常: {e}")
        return None


def is_valid_rain(code, pop, precip, is_dry_sunny):
    """
    严谨过滤假雨逻辑：
    1. 正常需要降水概率 >= 60% 且小时雨量 >= 0.8mm
    2. 如果当前属于晴热干燥（实况晴 + >=28℃ + 湿度<=65%），进一步要求 >= 70% 且雨量 >= 1.5mm
    """
    w_text = WMO_CODE_MAP.get(code, "多云")
    if "雨" not in w_text and "雪" not in w_text:
        return False, w_text

    if is_dry_sunny:
        if pop >= 70 and precip >= 1.5:
            return True, w_text
        return False, "多云" if code == 2 else "晴"

    if pop >= 60 and precip >= 0.8:
        return True, w_text
    return False, "多云" if code == 2 else "晴"


def summarize_period_weather(codes, pops, precips, is_dry_sunny):
    rain_hours = []
    clear_count, cloud_count, overcast_count = 0, 0, 0

    for code, pop, precip in zip(codes, pops, precips):
        has_rain, mapped_text = is_valid_rain(code, pop, precip, is_dry_sunny)
        if has_rain:
            rain_hours.append(mapped_text)
        else:
            if mapped_text == "晴" or code in (0, 1):
                clear_count += 1
            elif mapped_text == "多云" or code == 2:
                cloud_count += 1
            else:
                overcast_count += 1

    if rain_hours:
        return max(set(rain_hours), key=rain_hours.count)

    total = max(len(codes), 1)
    if clear_count / total >= 0.45:
        return "晴"
    elif (clear_count + cloud_count) / total >= 0.6:
        return "多云"
    else:
        return "阴"


def build_accurate_weather(city_input, amap_key):
    adcode, short_city, lon, lat = resolve_location(city_input, amap_key)
    live, cast = get_amap_weather(adcode, amap_key)
    om_data = get_hourly_precision_weather(lon, lat)

    live_weather = live.get("weather", "晴")
    live_temp = live.get("temperature")
    humidity_val = int(live.get("humidity", 55)) if str(live.get("humidity", "")).isdigit() else 55
    wind_dir = live.get("winddirection") or cast.get("daywind") or "西"
    wind_power = live.get("windpower") or cast.get("daypower") or "3"

    amap_day_w = cast.get("dayweather", live_weather)
    amap_night_w = cast.get("nightweather", live_weather)
    amap_max_t = cast.get("daytemp")
    amap_min_t = cast.get("nighttemp")

    # 判断实况是否为晴热干燥状态
    curr_temp_raw = float(live_temp) if (live_temp and str(live_temp).replace(".", "", 1).isdigit()) else 28.0
    is_dry_sunny = (live_weather == "晴" and curr_temp_raw >= 27.5 and humidity_val <= 65)

    rain_hours_list = []

    if om_data and "hourly" in om_data:
        hourly = om_data["hourly"]
        h_codes = hourly.get("weather_code", [])[:24]
        h_pops = [p if p is not None else 0 for p in hourly.get("precipitation_probability", [])[:24]]
        h_precips = [p if p is not None else 0.0 for p in hourly.get("precipitation", [])[:24]]
        h_temps = [t for t in hourly.get("temperature_2m", [])[:24] if t is not None]

        curr_temp = int(round(curr_temp_raw))
        real_live_weather = live_weather

        # 白天 (07:00 - 19:00) 与 夜间 (19:00 - 24:00) 天气判定
        day_weather = summarize_period_weather(h_codes[7:19], h_pops[7:19], h_precips[7:19], is_dry_sunny)
        night_weather = summarize_period_weather(h_codes[19:24], h_pops[19:24], h_precips[19:24], is_dry_sunny)

        # 检查全天是否有达到真实降雨级别的时段
        for hr in range(7, 24):
            if hr < len(h_codes):
                has_rain, r_str = is_valid_rain(h_codes[hr], h_pops[hr], h_precips[hr], is_dry_sunny)
                if has_rain:
                    rain_hours_list.append((hr, r_str))

        candidates_max = [curr_temp]
        candidates_min = [curr_temp]
        if h_temps:
            candidates_max.append(int(round(max(h_temps))))
            candidates_min.append(int(round(min(h_temps))))
        if amap_max_t and str(amap_max_t).lstrip("-").isdigit():
            candidates_max.append(int(amap_max_t))
        if amap_min_t and str(amap_min_t).lstrip("-").isdigit():
            candidates_min.append(int(amap_min_t))

        calc_max = int(round(max(candidates_max)))
        calc_min = int(round(min(candidates_min)))
        max_temp = max(calc_max, curr_temp)
        min_temp = min(calc_min, curr_temp)
    else:
        curr_temp = int(curr_temp_raw)
        max_temp = max(int(amap_max_t) if amap_max_t else curr_temp, curr_temp)
        min_temp = min(int(amap_min_t) if amap_min_t else curr_temp, curr_temp)
        real_live_weather = live_weather
        day_weather = "晴" if is_dry_sunny else amap_day_w
        night_weather = "多云" if is_dry_sunny else amap_night_w

    # 如果白天是晴天，夜间是多云或晴，直接统称为“晴”或“晴转多云”
    if day_weather == night_weather:
        weather_summary = f"{day_weather} (实时: {real_live_weather})"
    else:
        weather_summary = f"{day_weather}转{night_weather} (实时: {real_live_weather})"

    # 雨雪提醒
    if rain_hours_list:
        start_h = rain_hours_list[0][0]
        end_h = rain_hours_list[-1][0] + 1
        r_type = rain_hours_list[0][1]
        rain_snow_msg = f"{start_h}-{end_h}时有{r_type}，出门请带伞"
    elif "雨" in real_live_weather:
        rain_snow_msg = "当前有降雨，出门一定带伞"
    else:
        rain_snow_msg = "今日全天无雨雪，可放心出门~"

    temp_msg = f"{curr_temp}℃ (今日 {min_temp} ~ {max_temp}℃)"
    wind_dir_str = wind_dir if wind_dir.endswith("风") else f"{wind_dir}风"
    wind_msg = f"{wind_dir_str} {wind_power}级"

    if rain_hours_list or "雨" in real_live_weather:
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

    detail_url = f"https://m.baidu.com/s?word={quote(short_city + '天气预报')}"
    if "雨" in weather_summary:
        pic_url = "https://images.unsplash.com/photo-1515694346937-94d85e41e6f0?w=800&auto=format&fit=crop&q=80"
    else:
        pic_url = "https://images.unsplash.com/photo-1601297183305-6df142704ea2?w=800&auto=format&fit=crop&q=80"

    news_description = (
        f"今日天气：{weather_summary}\n"
        f"气温风向：{temp_msg} | {wind_msg}\n"
        f"雨雪提醒：{rain_snow_msg}\n"
        f"贴心提示：{tips_msg}\n"
        f"（点击卡片即可查看24小时趋势与15天预报）"
    )

    return {
        "city": short_city,
        "weather": weather_summary[:19],
        "rain_snow": rain_snow_msg[:19],
        "temp": temp_msg[:19],
        "wind": wind_msg[:19],
        "tips": tips_msg[:19],
        "detail_url": detail_url,
        "pic_url": pic_url,
        "news_title": f"【{short_city}天气】{weather_summary.split(' ')[0]} {curr_temp}℃（今日{min_temp}~{max_temp}℃）",
        "news_description": news_description
    }


def get_wechat_token(app_id, app_secret):
    url = "https://api.weixin.qq.com/cgi-bin/token"
    params = {"grant_type": "client_credential", "appid": app_id, "secret": app_secret}
    res = requests.get(url, params=params, timeout=10).json()
    if "access_token" not in res:
        raise RuntimeError(f"获取微信 token 失败: {res}")
    return res["access_token"]


def send_wechat_push(token, user_id, template_id, w_info):
    now_bj = get_beijing_now()
    date_str = f"{now_bj.year}年{now_bj.month}月{now_bj.day}日"

    # 1. 采用 UTF-8 发送图文封面大卡片
    custom_url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={token}"
    news_payload = {
        "touser": user_id,
        "msgtype": "news",
        "news": {
            "articles": [
                {
                    "title": w_info["news_title"],
                    "description": w_info["news_description"],
                    "url": w_info["detail_url"],
                    "picurl": w_info["pic_url"]
                }
            ]
        }
    }
    try:
        res_news = post_json_utf8(custom_url, news_payload)
        print(f"图文大封面卡片推送结果: {res_news}")
    except Exception as e:
        print(f"图文推送跳过: {e}")

    # 2. 采用 UTF-8 发送标准模板消息卡片
    tpl_url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={token}"
    tpl_payload = {
        "touser": user_id,
        "template_id": template_id,
        "url": w_info["detail_url"],
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
    res_tpl = post_json_utf8(tpl_url, tpl_payload)
    print(f"模板消息卡片推送结果: {res_tpl}")
    return res_tpl


def main():
    if not all([APP_ID, APP_SECRET, USER_ID, TEMPLATE_ID, AMAP_KEY]):
        print("错误：环境变量缺失，请检查 GitHub Secrets 配置！")
        sys.exit(1)

    weather_info = build_accurate_weather(CITY_NAME, AMAP_KEY)
    print("今日校准后纯净数据：", weather_info)

    token = get_wechat_token(APP_ID, APP_SECRET)
    for uid in [u.strip() for u in USER_ID.split(",") if u.strip()]:
        send_wechat_push(token, uid, TEMPLATE_ID, weather_info)


if __name__ == "__main__":
    main()
