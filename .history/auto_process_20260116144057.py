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
                
                # === Tophub 特殊逻辑：尝试获取晚报 ===
                if "tophub.today" in source['url']:
                    print("尝试检查是否有【晚报】内容...")
                    try:
                        content_early = await page.inner_text("body")

                        evening_btn = page.locator("a:has-text('晚报'), button:has-text('晚报')").first
                        if await evening_btn.count() == 0:
                            evening_btn = page.locator("text=晚报").first

                        if await evening_btn.count() > 0 and await evening_btn.first.is_visible():
                            print("发现【晚报】按钮，尝试切换...")
                            await evening_btn.first.click()
                            print("已点击【晚报】，等待内容切换...")

                            deadline = asyncio.get_running_loop().time() + 20.0
                            content_late = content_early
                            while asyncio.get_running_loop().time() < deadline:
                                await page.wait_for_timeout(500)
                                try:
                                    await page.wait_for_load_state('networkidle', timeout=1000)
                                except Exception:
                                    pass
                                content_late = await page.inner_text("body")
                                if content_late != content_early:
                                    print("成功获取【晚报】内容。正在合并早报与晚报...")
                                    return f"=== 早报内容 ===\n{content_early}\n\n=== 晚报内容 ===\n{content_late}"

                            print("未检测到内容变化（可能加载较慢或按钮未命中），将仅使用当前内容。")
                            return content_late

                        print("未找到可用的【晚报】切换按钮，使用当前页面内容。")
                        return content_early
                            
                    except Exception as e:
                        print(f"尝试切换晚报时出错: {e}。将仅使用当前内容。")
                        # 出错时回退到获取当前内容
                        pass
                # ========================================

            else:
                # 自动等待
                print("等待页面加载...")
                await page.wait_for_load_state('networkidle')
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
    
    prompt = f"""
请将以下新闻内容提取为 JSON 格式。

【格式要求】
1. 提供一个标题字段 "page_title"：
   - 标题结构：账号名（可选） + 时间/场景词（早间/今日速览） + 1-2个高关注度关键词 + 价值点（快速了解/关乎你我）
   - 字数限制：18-25字，手机端显示完整
2. 提取 15 条最热门、最重要的新闻。
3. 每条新闻必须包含以下三个字段：
   - "title": 新闻标题（简练有力）。
   - "summary_short": 两句话“讲解/思考”，用于生成图片，要求尽量一行显示完整：
        - 第一句：点出这条新闻的关键矛盾/看点（不要重复标题）
        - 第二句：给出一句解读/影响/启示（带一点观点）
        - 总字数建议 24-36 字，句间用“；”或“。”分隔
   - "summary_long": 深度分析与背景补充（约 100-120 字），用于公众号文章发表，要求内容充实、有观点。
4. 提取一句最有哲理的“金句”作为 'tip'。
5. 返回纯 JSON 格式，不要包含 Markdown 标记。

【内容】
{text[:15000]}
"""

    print("正在请求 AI 进行智能解析...")
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是一位资深新闻编辑与评论员，擅长用简短两句话给出讲解与观点。"},
                {"role": "user", "content": prompt}
            ],
            stream=False
        )
        
        result = response.choices[0].message.content
        result = result.replace("```json", "").replace("```", "").strip()
        
        # 针对性修复：只有当中文引号出现在键值对的分隔符位置时才替换
        import re
        # 替换键值对冒号后的开引号: : “ -> : "
        result = re.sub(r':\s*“', ': "', result)
        # 替换逗号前的闭引号: ”, -> ",
        result = re.sub(r'”\s*,', '",', result)
        # 替换对象结束前的闭引号: ”} -> "}
        result = re.sub(r'”\s*}', '"}', result)
        # 替换列表结束前的闭引号: ”] -> "]
        result = re.sub(r'”\s*]', '"]', result)
        
        # print(f"DEBUG: LLM 原始响应:\n{result}\n")
        
        data = json.loads(result)
        if not isinstance(data, dict):
            data = {"news_items": []}

        if isinstance(data.get("page_title"), dict):
            data["page_title"] = data["page_title"].get("text") or data["page_title"].get("title") or ""
        if "page_title" not in data:
            data["page_title"] = data.get("headline") or data.get("title_text") or ""
        if not isinstance(data.get("page_title"), str):
            data["page_title"] = ""
        data["page_title"] = data["page_title"].strip()
        if len(data["page_title"]) > 25:
            data["page_title"] = data["page_title"][:25]
        
        # 兼容性处理：如果返回了 news 而不是 news_items
        if 'news' in data and 'news_items' not in data:
            data['news_items'] = data['news']
            
        # 标准化：确保 news_items 是对象列表
        if 'news_items' in data and isinstance(data['news_items'], list):
            normalized_items = []
            for item in data['news_items']:
                if isinstance(item, str):
                    # 旧格式兼容（虽然 Prompt 要求了新格式，防万一）
                    parts = item.split('｜')
                    title = parts[0]
                    summary = parts[1] if len(parts) > 1 else ""
                    normalized_items.append({
                        "title": title,
                        "summary_short": summary[:30], # 截断作为短摘要
                        "summary_long": summary # 作为长摘要
                    })
                elif isinstance(item, dict):
                    # 确保字段齐全
                    if "summary_short" not in item:
                        item["summary_short"] = item.get("summary", "")[:30]
                    if "summary_long" not in item:
                        item["summary_long"] = item.get("summary", "")
                    normalized_items.append(item)
            data['news_items'] = normalized_items
        
        # 兼容性处理：将 tip 映射为 quote
        if 'tip' in data and 'quote' not in data:
            data['quote'] = {"text": data['tip'], "author": ""}

        # 兼容性处理：如果 quote 是字符串而不是对象
        if isinstance(data.get('quote'), str):
            data['quote'] = {"text": data['quote'], "author": ""}
        
        return data
    except json.JSONDecodeError:
        print(f"JSON 解析失败。原始响应: {result}")
        # 尝试使用正则提取结构化数据（兜底）
        try:
            import re
            # 尝试匹配完整的 item 结构
            item_pattern = r'"title":\s*"(.*?)".*?"summary_short":\s*"(.*?)".*?"summary_long":\s*"(.*?)"'
            matches = re.findall(item_pattern, result, re.S)
            
            if matches:
                print(f"通过正则找回 {len(matches)} 条结构化新闻")
                news_items = []
                for m in matches:
                    news_items.append({
                        "title": m[0],
                        "summary_short": m[1],
                        "summary_long": m[2]
                    })
                
                # 尝试提取 tip
                tip_match = re.search(r'"tip":\s*"(.*?)"', result)
                tip_text = tip_match.group(1) if tip_match else "保持热爱，奔赴山海。"
                
                return {
                    "news_items": news_items,
                    "quote": {"text": tip_text, "author": ""}
                }
            
            # 如果结构化提取失败，尝试提取所有字符串并尽力拼凑
            print("正则结构化提取失败，尝试提取所有字符串...")
            all_strings = re.findall(r'"([^"]+)"', result)
            if all_strings and len(all_strings) > 5:
                 # 假设前几个是 keys，我们很难准确恢复，返回一个空列表避免崩溃
                 return {
                    "news_items": [{"title": "数据解析错误", "summary_short": "请检查日志", "summary_long": f"原始数据片段: {all_strings[:3]}..."}],
                    "quote": {"text": "系统故障", "author": "Error"}
                 }
        except Exception as e:
            print(f"兜底解析也失败: {e}")
            pass
        return None
    except Exception as e:
        print(f"AI 分析失败: {e}")
        return None

def update_data_json(new_data):
    """
    更新 data.json 文件
    """
    try:
        with open('data.json', 'r', encoding='utf-8') as f:
            current_data = json.load(f)
        
        current_data['news_items'] = new_data.get('news_items', [])
        if isinstance(new_data.get("page_title"), str) and new_data["page_title"].strip():
            current_data["page_title"] = new_data["page_title"].strip()
        new_quote = new_data.get('quote')
        if isinstance(new_quote, str):
            current_data['quote']['text'] = new_quote
        elif isinstance(new_quote, dict):
             current_data['quote'] = new_quote

        today = datetime.datetime.now() - datetime.timedelta(days=1)
        week_list = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        current_data['date_info']['date_str'] = today.strftime("%Y年%m月%d日")
        current_data['date_info']['week_str'] = week_list[today.weekday()]
        
        with open('data.json', 'w', encoding='utf-8') as f:
            json.dump(current_data, f, ensure_ascii=False, indent=4)
            
        return current_data
    except Exception as e:
        print(f"更新 data.json 失败: {e}")
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

    # 更新 JSON
    try:
        current_data = update_data_json(data_processed)
        if current_data:
            print("data.json 已更新。")
            
            # === 新增：生成每日文章 ===
            try:
                data = current_data
                date_str = data['date_info']['date_str']
                # 替换日期中的中文字符以用于文件名（可选，这里直接用）
                filename = f"{date_str}.txt"
                article_dir = r"d:\zixun\每日文章"
                if not os.path.exists(article_dir):
                    os.makedirs(article_dir)
                
                article_path = os.path.join(article_dir, filename)
                
                page_title = data.get("page_title") or f"{date_str} 今日速览"
                article_content = f"【{page_title}】\n\n"
                article_content += f"日期：{date_str}\n"
                article_content += f"今日金句：{data['quote']['text']}\n"
                if data['quote']['author']:
                    article_content += f"—— {data['quote']['author']}\n"
                article_content += "\n" + "="*30 + "\n\n"
                
                for i, item in enumerate(data['news_items'], 1):
                    article_content += f"{i}. {item['title']}\n"
                    article_content += f"   {item['summary_long']}\n\n"
                
                with open(article_path, 'w', encoding='utf-8') as f:
                    f.write(article_content)
                    
                print(f"文章已生成: {article_path}")
            except Exception as e:
                print(f"生成文章失败: {e}")
            # ==========================
            
            print("正在调用 gen_image.py 生成图片...")
            import gen_image
            await gen_image.main()
            
    except Exception as e:
        print(f"处理流程出错: {e}")

if __name__ == "__main__":
    asyncio.run(main())
