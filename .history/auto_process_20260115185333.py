import asyncio
import json
import os
import re
import datetime
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# =================配置区域=================
# 请在这里填入你的 LLM API Key
API_KEY = "ms-b8df244e-aa5e-4392-b3bf-4b0e0f80c052" 
API_BASE_URL = "https://api-inference.modelscope.cn/v1" 
MODEL_NAME = "ZhipuAI/GLM-4.7" 

# 数据源配置
SOURCES = [
    {
        "name": "百度热搜",
        "url": "https://top.baidu.com/board?tab=realtime",
        "type": "direct_html", # 直接获取 HTML 解析
        "selector": "body" 
    },
    {
        "name": "Tophub Daily",
        "url": "https://tophub.today/daily",
        "type": "manual_captcha", # 需要手动验证
        "selector": "body"
    }
]
# =========================================

async def fetch_html_content(source):
    """
    通用 HTML 获取器
    """
    print(f"正在尝试从 [{source['name']}] 获取内容...")
    
    async with async_playwright() as p:
        # 百度热搜通常不需要复杂的验证，headless=True 即可
        # Tophub 需要 headless=False
        headless = source['type'] != 'manual_captcha'
        
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            await page.goto(source['url'], timeout=60000)
            
            if source['type'] == 'manual_captcha':
                print("\n" + "="*50)
                print("【需要手动验证】")
                print("浏览器已打开。请在弹出的浏览器窗口中完成验证，直到看到新闻列表。")
                print("="*50 + "\n")
                await asyncio.to_thread(input, ">> 确认页面已加载完毕？请按【回车键】继续程序...")
            else:
                # 自动等待
                await page.wait_for_timeout(3000)

            # 获取 HTML
            content = await page.content()
            
            # 使用 BeautifulSoup 提取主要内容，减少 token
            soup = BeautifulSoup(content, 'html.parser')
            
            # 针对不同源的简单清理
            if "baidu" in source['url']:
                # 百度热搜的主要内容在 main 或特定 class 中
                main_content = soup.find('div', class_='container') or soup.body
            else:
                main_content = soup.body
                
            # 转为文本 (保留一定的 HTML 结构可能更好，但纯文本更省 token)
            # 这里我们为了让 LLM 更好理解结构，提取纯文本但保留换行
            text_content = main_content.get_text(separator='\n', strip=True)
            
            print(f"成功获取内容，长度: {len(text_content)} 字符")
            return text_content
            
        except Exception as e:
            print(f"获取失败: {e}")
            return None
        finally:
            await browser.close()

async def process_with_llm(text):
    """
    调用 LLM API 对文本进行分析和总结 (仿照 60s-static-host 思路)
    """
    try:
        from openai import OpenAI
    except ImportError:
        print("错误：未安装 openai 库。")
        return None

    client = OpenAI(api_key=API_KEY, base_url=API_BASE_URL)
    
    # 仿照 60s-static-host 的 Prompt 结构，但针对我们的 JSON 格式进行调整
    prompt = f"""
    # Role
    你是一个专业的新闻编辑和数据解析专家。你的任务是从提供的网页文本中提取今日的热点新闻，并将其整理为指定的 JSON 格式。

    # Output Format (JSON)
    {{
      "news_items": [
        "标题1｜一句话深度分析（约15字）",
        "标题2｜一句话深度分析（约15字）",
        ... (提取 12-15 条)
      ],
      "quote": "一句富有哲理或激励人心的金句"
    }}

    # Requirements
    1. **新闻提取**:
       - 从文本中筛选出最具社会影响力、民生相关或科技突破的热点新闻。
       - 排除广告、娱乐八卦、重复内容。
       - 优先选择发生在国内的重要事件。

    2. **内容处理**:
       - **标题**: 必须简练有力，概括核心事件（不超过 20 字）。
       - **分析**: 在“｜”符号后，提供一句深度点评或背景补充，要有洞察力，避免废话。
       - **格式**: 严格遵守 "标题｜分析" 的格式。

    3. **数据源文本**:
    {text[:10000]} 
    """
    # 截取前 10000 字符，通常足够包含热搜列表

    print("正在请求 AI 进行智能解析...")
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "You are a helpful assistant designed to extract and structure news data."},
                {"role": "user", "content": prompt}
            ],
            stream=False
        )
        
        result = response.choices[0].message.content
        result = result.replace("```json", "").replace("```", "").strip()
        return json.loads(result)
    except Exception as e:
        print(f"AI 分析失败: {e}")
        return None

async def main():
    # 1. 尝试从不同源获取内容
    content = None
    for source in SOURCES:
        content = await fetch_html_content(source)
        if content and len(content) > 500: # 确保获取到了足够的内容
            break
        print(f"[{source['name']}] 获取内容过少或失败，尝试下一个源...")
    
    if not content:
        print("所有数据源均获取失败。")
        return

    # 2. 处理内容
    data_processed = await process_with_llm(content)
    if not data_processed:
        print("AI 处理失败。")
        return

    # 3. 更新 data.json
    try:
        with open('data.json', 'r', encoding='utf-8') as f:
            current_data = json.load(f)
        
        current_data['news_items'] = data_processed.get('news_items', [])
        new_quote = data_processed.get('quote')
        if isinstance(new_quote, str):
            current_data['quote']['text'] = new_quote
        elif isinstance(new_quote, dict):
             current_data['quote'] = new_quote

        today = datetime.datetime.now()
        week_list = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        current_data['date_info']['date_str'] = today.strftime("%Y年%m月%d日")
        current_data['date_info']['week_str'] = week_list[today.weekday()]
        
        with open('data.json', 'w', encoding='utf-8') as f:
            json.dump(current_data, f, ensure_ascii=False, indent=4)
            
        print("data.json 已更新。")
        
    except Exception as e:
        print(f"更新 data.json 失败: {e}")
        return

    # 4. 生成图片
    print("正在调用 gen_image.py 生成图片...")
    import gen_image
    await gen_image.main()

if __name__ == "__main__":
    asyncio.run(main())
