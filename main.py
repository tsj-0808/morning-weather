import os
import sys
import requests
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

# ================= 环境变量读取（完全兼容你现有的配置） =================
APP_ID = os.environ.get("APP_ID", "").strip()
APP_SECRET = os.environ.get("APP_SECRET", "").strip()
USER_ID = os.environ.get("USER_ID", "").strip()
TEMPLATE_ID = os.environ.get("TEMPLATE_ID", "").strip()
AMAP_KEY = os.environ.get("AMAP_KEY", "").strip()
CITY_NAME = (os.environ.get("CITY") or os.environ.get("CITY_NAME") or "红谷滩区").strip()

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
    """通过高德地理编码获取精准的 adcode、区县短名称、以及经纬度坐标 (lon, lat)"""
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
    """通过经纬度调用 Open-Meteo 高精度逐小时气象模型（ECMWF/GFS）"""
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
    """过滤低于35%概率或微小水汽的虚假降雨，还原真实天气"""
    rain_hours = []
    clear_count, cloud_count, overcast_count = 0, 0, 0

    for code, pop, precip in zip(codes, pops, precips):
        w_text = WMO_CODE_MAP.get(code, "多云")
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
        return max(set(rain_hours), key=rain_hours.count)

    total = max(len(codes), 1)
    if clear_count / total >= 0.5:
        return "晴"
    elif (clear_count + cloud_count) / total >= 0.6:
        return "多云" if cloud_count >= clear_count else "晴"
    else:
        return "阴"


def build_accurate_weather(city_input, amap_key):
    """多源融合与交叉校验，同时生成【卡片精简版】与【预览+完整详情版】数据"""
    adcode, short_city, lon, lat = resolve_location(city_input, amap_key)
    live, cast = get_amap_weather(adcode, amap_key)
    om_data = get_hourly_precision_weather(lon, lat)

    live_weather = live.get("weather", "晴")
    live_temp = live.get("temperature")
    humidity = live.get("humidity", "55")
    wind_dir = live.get("winddirection") or cast.get("daywind") or "微"
    wind_power = live.get("windpower") or cast.get("daypower") or "2"

    amap_day_w = cast.get("dayweather", live_weather)
    amap_night_w = cast.get("nightweather", live_weather)
    amap_max_t = cast.get("daytemp")
    amap_min_t = cast.get("nighttemp")

    rain_hours_list = []
    hourly_Slot_str = ""

    if om_data and "hourly" in om_data:
        hourly = om_data["hourly"]
        h_codes = hourly.get("weather_code", [])[:24]
        h_pops = [p if p is not None else 0 for p in hourly.get("precipitation_probability", [])[:24]]
        h_precips = [p if p is not None else 0.0 for p in hourly.get("precipitation", [])[:24]]
        h_temps = [t for t in hourly.get("temperature_2m", [])[:24] if t is not None]

        om_curr = om_data.get("current", {})
        om_curr_temp = om_curr.get("temperature_2m")
        om_curr_w = WMO_CODE_MAP.get(om_curr.get("weather_code", 0), "晴")
        if om_curr.get("relative_humidity_2m"):
            humidity = str(om_curr.get("relative_humidity_2m"))

        if live_temp is not None:
            curr_temp = int(round(float(live_temp)))
        elif om_curr_temp is not None:
            curr_temp = int(round(float(om_curr_temp)))
        else:
            curr_temp = 25

        morning_codes = h_codes[8:13]
        if live_weather in ("多云", "阴") and om_curr_w == "晴" and morning_codes.count(0) + morning_codes.count(1) >= 3:
            real_live_weather = "晴"
        else:
            real_live_weather = live_weather

        day_weather = summarize_period_weather(h_codes[7:19], h_pops[7:19], h_precips[7:19])
        night_weather = summarize_period_weather(h_codes[19:24], h_pops[19:24], h_precips[19:24])

        for hr in range(7, 24):
            if hr < len(h_codes):
                w_str = WMO_CODE_MAP.get(h_codes[hr], "")
                if ("雨" in w_str or "雪" in w_str) and h_pops[hr] >= 35 and h_precips[hr] >= 0.25:
                    rain_hours_list.append((hr, w_str, h_pops[hr]))

        # 生成早、中、晚关键时段的完整温度预览
        if len(hourly.get("temperature_2m", [])) >= 21:
            t_09 = int(round(hourly["temperature_2m"][9]))
            t_14 = int(round(hourly["temperature_2m"][14]))
            t_20 = int(round(hourly["temperature_2m"][20]))
            hourly_Slot_str = f"上午09时 {t_09}℃ | 下午14时 {t_14}℃ | 晚上20时 {t_20}℃"

        candidates_max = [curr_temp]
        candidates_min = [curr_temp]
        if h_temps:
            candidates_max.append(int(round(max(h_temps))))
            candidates_min.append(int(round(min(h_temps))))
        if amap_max_t and str(amap_max_t).lstrip("-").isdigit():
            candidates_max.append(int(amap_max_t))
        if amap_min_t and str(amap_min_t).lstrip("-").isdigit():
            candidates_min.append(int(amap_min_t))

        calc_max = int(round(sum(candidates_max[1:]) / len(candidates_max[1:]))) if len(candidates_max) > 1 else curr_temp
        calc_min = int(round(sum(candidates_min[1:]) / len(candidates_min[1:]))) if len(candidates_min) > 1 else curr_temp
        max_temp = max(calc_max, curr_temp)
        min_temp = min(calc_min, curr_temp)
    else:
        curr_temp = int(live_temp) if (live_temp and str(live_temp).lstrip("-").isdigit()) else 25
        max_temp = max(int(amap_max_t) if amap_max_t else curr_temp, curr_temp)
        min_temp = min(int(amap_min_t) if amap_min_t else curr_temp, curr_temp)
        real_live_weather = live_weather
        hum_val = int(humidity) if str(humidity).isdigit() else 60
        if "雨" in amap_day_w and "雨" not in real_live_weather and max_temp >= 28 and hum_val < 72:
            day_weather = real_live_weather
        else:
            day_weather = amap_day_w
        night_weather = amap_night_w

    # 1. 组装【今日天气】
    if day_weather == night_weather:
        short_w = day_weather
        weather_summary = f"{day_weather} (实时: {real_live_weather})"
    else:
        short_w = f"{day_weather}转{night_weather}"
        weather_summary = f"{short_w} (实时: {real_live_weather})"

    # 2. 组装【雨雪提醒】（短版用于模板卡片防截断，长版用于完整详情）
    if rain_hours_list:
        start_h = rain_hours_list[0][0]
        end_h = rain_hours_list[-1][0] + 1
        r_type = rain_hours_list[0][1]
        max_pop = max(x[2] for x in rain_hours_list)
        if start_h == end_h - 1:
            rain_snow_msg = f"约{start_h}时有{r_type}，出门记得带伞"
            full_rain_msg = f"今日约 {start_h}:00 预计有{r_type}（降水概率 {max_pop}%），出门千千万万记得带伞！"
        else:
            rain_snow_msg = f"{start_h}-{end_h}时有{r_type}，出门请带伞"
            full_rain_msg = f"今日 {start_h}:00~{end_h}:00 预计有{r_type}（降水概率 {max_pop}%），出门千千万万记得带伞！"
    elif "雨" in real_live_weather or "雪" in real_live_weather:
        rain_snow_msg = f"当前正在下{real_live_weather}，出门一定带伞"
        full_rain_msg = f"当前实时正在下{real_live_weather}，出门请务必携带雨具，注意路面湿滑！"
    else:
        rain_snow_msg = "今日全天无雨雪，可放心出门~"
        full_rain_msg = "今日全天无有效降雨（白天" + day_weather + "/夜间" + night_weather + "），可以放心出门~"

    # 3. 组装【当前气温】与【风向风力】
    temp_msg = f"{curr_temp}℃ (今日 {min_temp} ~ {max_temp}℃)"
    wind_dir_str = wind_dir if wind_dir.endswith("风") else f"{wind_dir}风"
    wind_msg = f"{wind_dir_str} {wind_power}级"

    # 4. 组装【贴心提示】（短版防截断，长版完整显示）
    if rain_hours_list or "雨" in real_live_weather:
        if max_temp >= 29:
            tips_msg = "今日闷热有雨，注意防暑与防滑"
            full_tips_msg = "今天气温较高且伴有降雨，体感偏闷热，出门请带好雨伞、注意防暑补水，路上注意安全！"
        elif min_temp <= 15:
            tips_msg = "今日有雨微凉，记得添衣带伞"
            full_tips_msg = "今天有雨且气温微凉，出门请记得添衣保暖并随身带伞，路上注意交通安全！"
        else:
            tips_msg = "今日有降雨，出门带伞注意安全"
            full_tips_msg = "今天有降雨天气，出门前请带好雨伞，骑车或步行注意路面防滑，路上注意安全！"
    else:
        if max_temp >= 30:
            tips_msg = "今天阳光较好气温高，注意防晒补水"
            full_tips_msg = "今天气温较高、紫外线较强，出门请注意防晒并多喝水补充水分，祝敏今天心情愉快！"
        elif min_temp <= 12:
            tips_msg = "早晚气温偏低，出门注意添衣保暖"
            full_tips_msg = "早晚气温偏低，出门记得穿件保暖外套，谨防着凉感冒，祝敏今天顺顺利利！"
        elif max_temp - min_temp >= 10:
            tips_msg = "今日昼夜温差较大，注意适时增减衣物"
            full_tips_msg = f"今日昼夜温差达 {max_temp - min_temp}℃，早晚偏凉中午暖，建议采用洋葱式穿衣法适时增减衣物~"
        else:
            tips_msg = "今日天气舒适宜人，祝心情愉快~"
            full_tips_msg = "今天风和日丽、温度非常舒适，很适合外出活动，祝敏拥有元气满满的一天！"

    # 5. 生成点击卡片跳转的完整可视化天气详情页链接（免服务器，微信内直接打开24小时与15天详情）
    detail_url = f"https://m.baidu.com/s?word={quote(short_city + '天气预报')}"

    # 6. 生成外层服务号列表的【单行预览】与点进去后的【完整天气详情】
    # 第一行会直接显示在微信外层「服务号」预览栏上
    outer_preview_line = f"【{short_city}】{short_w} {curr_temp}℃({min_temp}~{max_temp}℃)｜{rain_snow_msg}"

    full_detail_lines = [
        outer_preview_line,
        "━━━━━━━━━━━━━━",
        "📋 敏今日份完整天气详情",
        f"📍 所在地区：{short_city}",
        f"🌤 今日天气：{weather_summary}",
        f"🌡 全天气温：{temp_msg}（湿度 {humidity}%）",
    ]
    if hourly_Slot_str:
        full_detail_lines.append(f"🕒 时段温度：{hourly_Slot_str}")
    full_detail_lines.extend([
        f"☔ 雨雪详情：{full_rain_msg}",
        f"🍃 风向风力：{wind_msg}",
        f"💡 贴心提示：{full_tips_msg}",
        f"👉 <a href=\"{detail_url}\">点击查看24小时逐时曲线与15天预报</a>"
    ])

    return {
        "city": short_city,
        "weather": weather_summary[:19],
        "rain_snow": rain_snow_msg[:19],
        "temp": temp_msg[:19],
        "wind": wind_msg[:19],
        "tips": tips_msg[:19],
        "detail_url": detail_url,
        "full_text": "\n".join(full_detail_lines)
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
    """
    1. 发送原有的微信模板消息卡片（绑定 url，点击卡片即可查看完整可视化天气）
    2. 同步发送客服消息预览（让微信外层服务号列表直接显示今日天气摘要，点进去看完整无省略文字）
    """
    now_bj = get_beijing_now()
    date_str = f"{now_bj.year}年{now_bj.month}月{now_bj.day}日"

    # --- 第一步：发送原有的模板消息卡片（功能完全保留，增加点击卡片跳转详情） ---
    tpl_url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={token}"
    tpl_payload = {
        "touser": user_id,
        "template_id": template_id,
        "url": w_info["detail_url"],  # 点击卡片直接打开完整24小时/15天天气页
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
    res_tpl = requests.post(tpl_url, json=tpl_payload, timeout=10).json()
    print(f"模板卡片推送给 {user_id} 结果: {res_tpl}")

    # --- 第二步：发送外层预览与完整天气详情消息 ---
    # 注：只要用户在48小时内打开过该测试号（例如自动上报过定位），就会触发外层服务号红点+首行天气预览
    custom_url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={token}"
    custom_payload = {
        "touser": user_id,
        "msgtype": "text",
        "text": {
            "content": w_info["full_text"]
        }
    }
    try:
        res_custom = requests.post(custom_url, json=custom_payload, timeout=10).json()
        print(f"外层预览与完整详情推送给 {user_id} 结果: {res_custom}")
    except Exception as e:
        print(f"外层预览推送跳过: {e}")

    return res_tpl


def main():
    if not all([APP_ID, APP_SECRET, USER_ID, TEMPLATE_ID, AMAP_KEY]):
        print("错误：环境变量缺失，请检查 GitHub Secrets 配置！")
        sys.exit(1)

    weather_info = build_accurate_weather(CITY_NAME, AMAP_KEY)
    print("校准后天气数据：", weather_info)

    token = get_wechat_token(APP_ID, APP_SECRET)
    for uid in [u.strip() for u in USER_ID.split(",") if u.strip()]:
        send_wechat_push(token, uid, TEMPLATE_ID, weather_info)


if __name__ == "__main__":
    main()
