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

# 目标网址
TARGET_URL = "https://tophub.today/daily"
# =========================================

async def fetch_content():
    """
    使用 Playwright 获取网页内容。
    设置为 headless=False 以便用户手动处理验证码。
    """
    print(f"正在启动浏览器访问 {TARGET_URL} ...")
    print("注意：如果遇到验证码，请手动在弹出的浏览器窗口中完成验证！脚本将等待...")
    
    async with async_playwright() as p:
        # 启动有头浏览器，方便用户交互
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        try:
            await page.goto(TARGET_URL, timeout=60000) # 60秒超时
            
            # 等待页面加载关键内容。
            # 这里我们检测是否存在具体的日期文本或列表项，以此判断是否加载成功
            # 也可以简单地等待用户确认，或者等待特定的 DOM 元素
            print("正在等待页面加载... (如果卡在验证码，请手动处理)")
            
            # 尝试等待一个标志性的元素，比如包含日期的标题
            try:
                await page.wait_for_selector("div", state="visible", timeout=30000)
            except:
                print("等待超时，尝试直接获取内容...")

            # 简单的策略：获取页面上的所有文本，交给 LLM 清洗
            # 为了获取更有结构的数据，我们尝试获取主要的内容区域
            # 根据经验，通常在 body 下
            content_text = await page.inner_text("body")
            
            # 简单的清洗，去掉多余的空行
            lines = [line.strip() for line in content_text.split('\n') if line.strip()]
            clean_text = '\n'.join(lines)
            
            print(f"成功获取页面内容，长度: {len(clean_text)} 字符")
            return clean_text
            
        except Exception as e:
            print(f"获取页面失败: {e}")
            return None
        finally:
            await browser.close()

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
