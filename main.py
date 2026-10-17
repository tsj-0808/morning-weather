import os
import requests
import datetime

# 从 GitHub 环境变量读取配置
APP_ID = os.environ.get("APP_ID")
APP_SECRET = os.environ.get("APP_SECRET")
USER_ID = os.environ.get("USER_ID")
TEMPLATE_ID = os.environ.get("TEMPLATE_ID")
AMAP_KEY = os.environ.get("AMAP_KEY")
CITY = os.environ.get("CITY", "北京市")

def get_weather(city_name):
    # 获取高德实时天气
    live_url = f"https://restapi.amap.com/v3/weather/weatherInfo?key={AMAP_KEY}&city={city_name}&extensions=base"
    # 获取高德全天预报天气（包含白天、夜间天气及最高最低温）
    forecast_url = f"https://restapi.amap.com/v3/weather/weatherInfo?key={AMAP_KEY}&city={city_name}&extensions=all"
    
    live_res = requests.get(live_url).json()
    forecast_res = requests.get(forecast_url).json()
    
    live_data = live_res["lives"][0]
    today_forecast = forecast_res["forecasts"][0]["casts"][0]
    
    live_weather = live_data["weather"]
    day_weather = today_forecast["dayweather"]
    night_weather = today_forecast["nightweather"]
    
    # 组合全天天气描述（如：晴转多云，当前晴）
    if day_weather == night_weather:
        full_weather = f"{day_weather}（实时：{live_weather}）"
    else:
        full_weather = f"{day_weather}转{night_weather}（实时：{live_weather}）"

    # 判断全天（实时 + 白天 + 夜间）是否下雨或下雪
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
    return requests.get(url).json().get("access_token")

def send_wechat():
    weather_info = get_weather(CITY)
    tips = generate_tips(weather_info["rain_snow"], weather_info["max_temp"], weather_info["min_temp"])
    
    # 强制转换为北京时间 (UTC+8)
    tz = datetime.timezone(datetime.timedelta(hours=8))
    today_str = datetime.datetime.now(tz).strftime("%Y年%m月%d日")
    
    token = get_access_token()
    send_url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={token}"
    
    for uid in USER_ID.split(","):
        payload = {
            "touser": uid.strip(),
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
        print(f"Send to {uid}: {res}")

if __name__ == "__main__":
    send_wechat()
