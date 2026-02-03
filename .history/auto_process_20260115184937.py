import asyncio
import json
import os
import re
import datetime
from playwright.async_api import async_playwright

# =================配置区域=================
# 请在这里填入你的 LLM API Key (例如 OpenAI, DeepSeek, Moonshot 等)
# 如果留空，将使用简单的文本提取（效果可能不如 AI 总结好）
API_KEY = "ms-b8df244e-aa5e-4392-b3bf-4b0e0f80c052" 
API_BASE_URL = "https://api-inference.modelscope.cn/v1" # 如果是其他厂商，请修改此 URL
MODEL_NAME = "ZhipuAI/GLM-4.7" # 或 deepseek-chat, moonshot-v1 等

# 目标数据源路径
DATA_SOURCE_DIR = r"d:\zixun\60s-static-host\static\60s"

async def fetch_content():
    """
    从本地 60s-static-host 项目中读取今日新闻数据。
    """
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    file_path = os.path.join(DATA_SOURCE_DIR, f"{today_str}.json")
    
    print(f"正在尝试读取本地数据文件: {file_path}")
    
    if not os.path.exists(file_path):
        print(f"错误：找不到今日 ({today_str}) 的数据文件。")
        print("请确认 '60s-static-host' 项目已更新到最新数据。")
        # 尝试寻找最近的一个文件作为 fallback
        try:
            files = sorted([f for f in os.listdir(DATA_SOURCE_DIR) if f.endswith('.json')])
            if files:
                latest_file = files[-1]
                print(f"尝试使用最近的一份数据: {latest_file}")
                file_path = os.path.join(DATA_SOURCE_DIR, latest_file)
            else:
                return None
        except Exception as e:
            print(f"查找文件失败: {e}")
            return None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        news_list = data.get('news', [])
        tip = data.get('tip', '')
        
        # 将新闻列表转换为文本格式供 AI 处理
        content_text = f"今日金句：{tip}\n\n新闻列表：\n"
        for idx, news in enumerate(news_list, 1):
            content_text += f"{idx}. {news}\n"
            
        print(f"成功读取本地数据，共 {len(news_list)} 条新闻。")
        return content_text
        
    except Exception as e:
        print(f"读取或解析文件失败: {e}")
        return None

async def process_with_llm(text):
    """
    调用 LLM API 对文本进行分析和总结
    """
    try:
        from openai import OpenAI
    except ImportError:
        print("错误：未安装 openai 库。请运行 'pip install openai'。")
        return None

    if "sk-xxxx" in API_KEY:
        print("警告：未配置有效的 API Key。跳过 AI 分析步骤。")
        return None

    client = OpenAI(api_key=API_KEY, base_url=API_BASE_URL)
    
    today_str = datetime.datetime.now().strftime("%Y年%m月%d日")
    
    prompt = f"""
    你是一个专业的新闻编辑。请从以下网页文本中提取今日的热点新闻。
    
    任务要求：
    1. 提取约 12-15 条最重要的社会/科技/民生热点。
    2. 将这些热点分为 4-5 个领域（如：民生保障、交通出行、科技财经、国际动态等）。
    3. 对每条新闻，生成一个“标题”和一个“简短分析”。
    4. 格式要求：
       - 标题要简练有力。
       - 分析要一针见血，约 10-20 字。
       - 输出结果必须是标准的 JSON 格式，结构如下：
         {{
           "news_items": [
             "标题1｜分析1",
             "标题2｜分析2",
             ...
           ],
           "quote": "一句励志或深度的金句"
         }}
    
    网页文本内容：
    {text[:8000]} 
    """
    # 截取前8000字符避免token溢出，通常热点在前面

    print("正在请求 AI 进行分析和总结...")
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that extracts news and formats them as JSON."},
                {"role": "user", "content": prompt}
            ],
            stream=False # 关闭流式输出，方便解析 JSON
        )
        
        result = response.choices[0].message.content
        # 尝试清理可能存在的 Markdown 代码块标记
        result = result.replace("```json", "").replace("```", "").strip()
        return json.loads(result)
    except Exception as e:
        print(f"AI 分析失败: {e}")
        return None

def fallback_parsing(text):
    """
    如果 AI 失败或未配置，使用简单的正则提取
    """
    print("使用基础规则提取新闻...")
    # 假设文本中包含类似 "1. xxxx" 或简单的换行
    # 这里做一个简单的模拟，实际可能需要根据页面具体文本特征调整
    lines = text.split('\n')
    news_items = []
    
    # 尝试寻找长句子作为新闻
    for line in lines:
        if len(line) > 10 and len(line) < 50 and not line.startswith("http"):
             # 简单的过滤
            if len(news_items) < 12:
                news_items.append(f"{line}｜详情待补充")
    
    return {
        "news_items": news_items,
        "quote": "坚持就是胜利。"
    }

async def main():
    # 1. 获取内容
    content = await fetch_content()
    if not content:
        return

    # 2. 处理内容 (AI 或 Fallback)
    data_processed = await process_with_llm(content)
    if not data_processed:
        data_processed = fallback_parsing(content)

    # 3. 更新 data.json
    try:
        with open('data.json', 'r', encoding='utf-8') as f:
            current_data = json.load(f)
        
        # 更新新闻列表和语录
        current_data['news_items'] = data_processed.get('news_items', [])
        
        # 更新语录（如果有）
        new_quote = data_processed.get('quote')
        if isinstance(new_quote, str):
            current_data['quote']['text'] = new_quote
        elif isinstance(new_quote, dict):
             current_data['quote'] = new_quote

        # 更新日期
        today = datetime.datetime.now()
        week_list = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        current_data['date_info']['date_str'] = today.strftime("%Y年%m月%d日")
        current_data['date_info']['week_str'] = week_list[today.weekday()]
        # 农历转换比较复杂，这里暂时保持原样或需要引入第三方库，或者让 AI 生成
        
        with open('data.json', 'w', encoding='utf-8') as f:
            json.dump(current_data, f, ensure_ascii=False, indent=4)
            
        print("data.json 已更新。")
        
    except Exception as e:
        print(f"更新 data.json 失败: {e}")
        return

    # 4. 生成图片
    print("正在调用 gen_image.py 生成图片...")
    # 直接调用 gen_image.py 的逻辑，或者通过命令行调用
    import gen_image
    await gen_image.main()

if __name__ == "__main__":
    asyncio.run(main())
