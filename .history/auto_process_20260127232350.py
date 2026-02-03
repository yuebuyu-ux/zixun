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
                print("【智能等待加载】")
                print("正在等待页面加载... 如遇验证码请手动完成。")
                print("="*50 + "\n")
                
                # 1. 尝试自动等待加载完成
                try:
                    await page.wait_for_load_state('networkidle', timeout=15000)
                except:
                    print("等待超时，继续检查内容...")

                # 2. 检查当前内容是否有效
                temp_text = await page.inner_text("body")
                if len(temp_text) < 500 or "Just a moment" in temp_text:
                    print("检测到内容过短或包含验证提示，暂停等待人工介入...")
                    await asyncio.to_thread(input, ">> 请在浏览器中完成验证并显示新闻列表后，按【回车键】继续...")
                else:
                    print(f"页面似乎已加载 (内容长度: {len(temp_text)})，自动继续...")
                
                # === Tophub 特殊逻辑：尝试获取晚报 ===
                if "tophub.today" in source['url']:
                    print("尝试检查是否有【晚报】内容...")
                    try:
                        content_early = await page.inner_text("body")
                        print(f"【当前早报】字数: {len(content_early)}")
                        print("【需要人工介入】请在浏览器中手动点击正确的【晚报】按钮，确认内容更新。")
                        await asyncio.to_thread(input, ">> 手动切换完成后，请按【回车键】继续...")

                        content_final = await page.inner_text("body")
                        print(f"人工确认后内容字数: {len(content_final)}")

                        if len(content_final) != len(content_early):
                            merged = f"=== 早报内容 ===\n{content_early}\n\n=== 晚报内容 ===\n{content_final}"
                            print(f"合并后总字数: {len(merged)}")
                            return merged

                        print("内容仍未变化，将仅使用当前内容。")
                        return content_final

                    except Exception as e:
                        print(f"尝试切换晚报时出错: {e}。将仅使用当前内容。")
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
    
    # 计算发布日期（明天）
    publish_date = datetime.datetime.now() + datetime.timedelta(days=1)
    publish_date_str = publish_date.strftime("%Y年%m月%d日")

    prompt = f"""
请将以下新闻内容提取为 JSON 格式，适配早间公众号新闻总结场景，突出“权威、实用、易读”。

【日期规则】
1. 本内容将于 **{publish_date_str}** 发布。
2. 文中所有涉及时间的内容，**必须转换为绝对日期**（如“1月18日”），**严禁**使用“明日”、“明天”、“下周”、“后天”等相对时间名词，避免读者产生时间错乱。
3. 如果原文说是“明天开始”，请根据当前日期推算具体是哪一天并写明。

【筛选规则】
1.  优先选择 **民生政策、行业大事、正能量社会新闻、重要科技/经济动态**；
2.  剔除内容：灾难事故、负面暴力、敏感政治、无意义八卦；
3.  筛选标准：优先从当日热搜Top30、权威媒体头版中选取，确保15条内容覆盖多领域（不重复同一主题）。
4.  内容唯一性要求：严禁出现标题、核心事实（如事件主体、关键数据、时间）完全一致的重复内容，相似主题需差异化表述（如不同地区的民生政策）；
5.  兜底机制：若当日符合要求的有效新闻不足15条，按实际数量输出（无需凑数），优先保留高关注度领域内容。
【格式要求】
1.  提供一个标题字段 "page_title"：
    - 结构：60 s 看懂世界 +  1-2个高关注度关键词
    - 字数：18-25字，手机端显示完整，避免“今日新闻汇总”类平淡表述
    - 风格：简洁有力，带正向引导，
2.  生成 1 个开头文案字段 "opening"：
    - 风格：干练实用，符合早间快速读新闻的节奏，30-45字
    - 内容：时间提醒+1-2个核心新闻钩子+阅读引导，点明“高效速览、关乎民生”的属性
    - 点缀：可加1个提示类emoji（如📌/⏰）
3.  生成 1 个结尾文案字段 "ending"：
    - 风格：温和正向，带引导性，40-55字
    - 内容：总结价值+互动提问（可选）+星标引导
    - 点缀：可加1个引导类emoji（如⭐/🔔）
4.  提取 15 条新闻，放入 "news_items" 数组中（不足则按实际数量输出）。每条必须包含以下三个字段：
    - "title"：新闻标题（≤20字，简练有力，提取核心事实/数据/突破点，不堆砌修饰词）；
    - "summary_short"：图片配套文案（24-32字，必须一行显示完整）：
        - 第一句：点出新闻核心看点/关键变化（不重复标题）；
        - 第二句：给出一句解读/影响/启示（带一点观点）；
        - 句间用“；”分隔，
    - "summary_long"：深度分析与背景补充（100-120字）：
        - 内容：充实有观点，包含背景、细节、影响；
        - 风格：客观权威，避免口语化。
5.  提取一句最有哲理的“金句”作为 "tip"：
    - 风格：正向有深度，贴合早间阅读氛围；
    - 字数：15-20字，不带作者，避免鸡汤化。

【输出要求】
- 只返回纯 JSON 格式，无任何 Markdown 标记、无多余解释文字；
- 语言风格统一：正式且易懂，适配早间新闻的权威感与实用性。

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

        if isinstance(data.get("opening"), dict):
            data["opening"] = data["opening"].get("text") or data["opening"].get("content") or ""
        if "opening" not in data:
            data["opening"] = data.get("intro") or data.get("lead") or ""
        if not isinstance(data.get("opening"), str):
            data["opening"] = ""
        data["opening"] = data["opening"].strip()

        if isinstance(data.get("ending"), dict):
            data["ending"] = data["ending"].get("text") or data["ending"].get("content") or ""
        if "ending" not in data:
            data["ending"] = data.get("outro") or data.get("closing") or ""
        if not isinstance(data.get("ending"), str):
            data["ending"] = ""
        data["ending"] = data["ending"].strip()
        
        # 兼容性处理：寻找可能的列表键名
        if 'news_items' not in data:
            for key in ['news', 'items', 'list', 'data', 'contents']:
                if key in data and isinstance(data[key], list):
                    data['news_items'] = data[key]
                    break
            
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
        
        if not data.get('news_items'):
             print("警告: 未能从 AI 响应中提取到任何新闻条目 (news_items 为空)。")
             print(f"AI 返回的顶层键: {list(data.keys())}")

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
        if isinstance(new_data.get("opening"), str):
            current_data["opening"] = new_data["opening"].strip()
        if isinstance(new_data.get("ending"), str):
            current_data["ending"] = new_data["ending"].strip()
        new_quote = new_data.get('quote')
        if isinstance(new_quote, str):
            current_data['quote']['text'] = new_quote
        elif isinstance(new_quote, dict):
             current_data['quote'] = new_quote

        today = datetime.datetime.now() + datetime.timedelta(days=1)
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

                opening = (data.get("opening") or "").strip()
                if opening:
                    article_content += f"{opening}\n\n"
                
                for i, item in enumerate(data['news_items'], 1):
                    article_content += f"{i}. {item['title']}\n"
                    article_content += f"   {item['summary_long']}\n\n"

                ending = (data.get("ending") or "").strip()
                if ending:
                    article_content += f"{ending}\n"
                
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
