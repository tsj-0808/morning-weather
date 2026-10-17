import os
import sys
import requests
import datetime

# 读取环境变量并自动去除空格
APP_ID = (os.environ.get("APP_ID") or "").strip()
APP_SECRET = (os.environ.get("APP_SECRET") or "").strip()
USER_ID = (os.environ.get("USER_ID") or "").strip()
TEMPLATE_ID = (os.environ.get("TEMPLATE_ID") or "").strip()
AMAP_KEY = (os.environ.get("AMAP_KEY") or "").strip()
CITY = (os.environ.get("CITY") or "").strip() or "北京市"

def resolve_adcode(address_str):
    """当直接用长地址查不到天气时，自动解析详细地址所属的区县编码(adcode)"""
    geo_url = f"https://restapi.amap.com/v3/geocode/geo?key={AMAP_KEY}&address={address_str}"
    res = requests.get(geo_url).json()
    if res.get("status") == "1" and res.get("geocodes"):
        adcode = res["geocodes"][0].get("adcode")
        district = res["geocodes"][0].get("district") or res["geocodes"][0].get("city")
        print(f"📍 已自动将详细地址【{address_str}】定位到所属区域：【{district}】(编码: {adcode})")
        return adcode
    return address_str

def get_weather(city_name):
    print(f"👉 正在查询【{city_name}】的天气...")
    query_target = city_name
    
    live_url = f"https://restapi.amap.com/v3/weather/weatherInfo?key={AMAP_KEY}&city={query_target}&extensions=base"
    live_res = requests.get(live_url).json()
    
    # 如果因为填了学校/详细地址导致 lives 为空，自动解析所在区县再查一次
    if live_res.get("status") == "1" and not live_res.get("lives"):
        query_target = resolve_adcode(city_name)
        live_url = f"https://restapi.amap.com/v3/weather/weatherInfo?key={AMAP_KEY}&city={query_target}&extensions=base"
        live_res = requests.get(live_url).json()

    forecast_url = f"https://restapi.amap.com/v3/weather/weatherInfo?key={AMAP_KEY}&city={query_target}&extensions=all"
    forecast_res = requests.get(forecast_url).json()
    
    if live_res.get("status") != "1" or not live_res.get("lives"):
        print(f"❌ 高德实时天气接口报错！返回信息：{live_res}")
        sys.exit(1)

    if forecast_res.get("status") != "1" or not forecast_res.get("forecasts"):
        print(f"❌ 高德预报天气接口报错！返回信息：{forecast_res}")
        sys.exit(1)
        
    live_data = live_res["lives"][0]
    today_forecast = forecast_res["forecasts"][0]["casts"][0]
    
    live_weather = live_data["weather"]
    day_weather = today_forecast["dayweather"]
    night_weather = today_forecast["nightweather"]
    
    if day_weather == night_weather:
        full_weather = f"{day_weather}（实时：{live_weather}）"
    else:
        full_weather = f"{day_weather}转{night_weather}（实时：{live_weather}）"

    all_weather_str = f"{live_weather}{day_weather}{night_weather}"
    if "雨" in all_weather_str and "雪" in all_weather_str:
        rain_snow_status = f"🌨️ 今日有雨雪（白天{day_weather}/夜间{night_weather}），务必带伞防滑！"
    elif "雪" in all_weather_str:
        rain_snow_status = f"❄️ 今日有雪（白天{day_weather}/夜间{night_weather}），出门注意防寒防滑！"
    elif "雨" in all_weather_str:
        rain_snow_status = f"☔ 今日有雨（白天{day_weather}/夜间{night_weather}），出门千万记得带伞！"
    else:
        rain_snow_status = "☀️ 今日全天无雨雪，可以放心出门~"

    return {
        "city": live_data["city"],
        "weather": full_weather,
        "rain_snow": rain_snow_status,
        "temp": live_data["temperature"],
        "min_temp": today_forecast["nighttemp"],
        "max_temp": today_forecast["daytemp"],
        "wind": f"{live_data['winddirection']}风 {live_data['windpower']}级"
    }

def generate_tips(rain_snow, max_temp, min_temp):
    tips = []
    if int(max_temp) >= 30:
        tips.append("今天气温较高，注意防晒补水🌞")
    elif int(min_temp) <= 10:
        tips.append("早晚气温较低，记得多穿一件外套🧥")
    else:
        tips.append("今天温度适宜，祝你拥有元气满满的一天✨")
        
    if "有雨" in rain_snow or "有雪" in rain_snow:
        tips.append("路上注意安全哦")
        
    return "，".join(tips)

def get_access_token():
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={APP_ID}&secret={APP_SECRET}"
    res = requests.get(url).json()
    if "access_token" not in res:
        print(f"❌ 微信获取 Token 失败！返回信息：{res}")
        print("💡 排查建议：请检查 Secrets 里的 APP_ID 和 APP_SECRET 是否与微信测试号后台完全一致！")
        sys.exit(1)
    return res.get("access_token")

def send_wechat():
    weather_info = get_weather(CITY)
    tips = generate_tips(weather_info["rain_snow"], weather_info["max_temp"], weather_info["min_temp"])
    
    tz = datetime.timezone(datetime.timedelta(hours=8))
    today_str = datetime.datetime.now(tz).strftime("%Y年%m月%d日")
    
    token = get_access_token()
    send_url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={token}"
    
    for uid in USER_ID.split(","):
        uid = uid.strip()
        if not uid:
            continue
        payload = {
            "touser": uid,
            "template_id": TEMPLATE_ID,
            "data": {
                "date": {"value": today_str},
                "city": {"value": weather_info["city"]},
                "weather": {"value": weather_info["weather"]},
                "rain_snow": {"value": weather_info["rain_snow"]},
                "temp": {"value": weather_info["temp"]},
                "min_temp": {"value": weather_info["min_temp"]},
                "max_temp": {"value": weather_info["max_temp"]},
                "wind": {"value": weather_info["wind"]},
                "tips": {"value": tips}
            }
        }
        res = requests.post(send_url, json=payload).json()
        if res.get("errcode") == 0:
            print(f"✅ 成功发送给微信用户 {uid}！")
        else:
            print(f"❌ 微信推送失败！返回信息：{res}")
            print("💡 排查建议：请检查 USER_ID（是不是以 o 开头的那串微信号）和 TEMPLATE_ID 是否填对！")
            sys.exit(1)

if __name__ == "__main__":
    send_wechat()
