import asyncio
import json
import os
import re
import datetime
import argparse
import sys
import urllib.parse
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
                
                if "tophub.today" in source['url'] and "daily" in source['url']:
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

def build_hot_items(text, max_items=120):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items = []
    i = 0
    while i < len(lines):
        if re.fullmatch(r"\d{1,3}", lines[i]):
            try:
                rank = int(lines[i])
            except Exception:
                i += 1
                continue
            title = lines[i + 1] if i + 1 < len(lines) else ""
            j = i + 2
            source = ""
            heat = ""
            if j < len(lines) and ("·" in lines[j] or "‧" in lines[j]):
                source = lines[j].replace("·", "").replace("‧", "").strip()
                j += 1
            if j < len(lines) and ("热度" in lines[j] or re.search(r"\d", lines[j])):
                heat = lines[j].strip()
                j += 1
            if title and not re.fullmatch(r"\d{1,3}", title):
                items.append({"rank": rank, "title": title, "source": source, "heat": heat})
            i = j
        else:
            i += 1

    if not items:
        candidate_lines = []
        for line in lines[:1000]:
            if len(line) < 6:
                continue
            if "热度" in line:
                continue
            candidate_lines.append(line)
        items = [{"rank": idx + 1, "title": t, "source": "", "heat": ""} for idx, t in enumerate(candidate_lines[:max_items])]

    return items[:max_items]

def build_hot_digest(text, max_items=120):
    items = build_hot_items(text, max_items=max_items)
    digest_lines = []
    for item in items[:max_items]:
        meta = []
        if item.get("source"):
            meta.append(item["source"])
        if item.get("heat") and "热度" in item["heat"]:
            meta.append(item["heat"])
        meta_str = f"（{'，'.join(meta)}）" if meta else ""
        digest_lines.append(f"{item['rank']}. {item['title']}{meta_str}")
    return "\n".join(digest_lines)

def _get_env(name, default_value):
    value = os.environ.get(name)
    if value is None:
        return default_value
    value = value.strip()
    return value if value else default_value

def _make_openai_client():
    try:
        from openai import OpenAI
    except ImportError:
        return None
    api_key = _get_env("API_KEY", API_KEY)
    api_base_url = _get_env("API_BASE_URL", API_BASE_URL)
    return OpenAI(api_key=api_key, base_url=api_base_url)

def _clean_json_text(text):
    result = (text or "").replace("```json", "").replace("```", "").strip()
    result = re.sub(r':\s*“', ': "', result)
    result = re.sub(r'”\s*,', '",', result)
    result = re.sub(r'”\s*}', '"}', result)
    result = re.sub(r'”\s*]', '"]', result)
    return result

async def process_with_llm(text):
    """
    调用 LLM API 对文本进行分析和总结 (仿照 60s-static-host 思路)
    """
    client = _make_openai_client()
    if not client:
        print("错误：未安装 openai 库。")
        return None
    
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
5.  排序要求：将国家政策、重要民生政策、重大行业政策相关内容放在最前面输出，其次再输出其他类别。
6.  兜底机制：若当日符合要求的有效新闻不足15条，按实际数量输出（无需凑数），优先保留高关注度领域内容。
【格式要求】
1.  提供一个标题字段 "page_title"：
    - 结构：60s 看懂世界 + ｜ + 高关注度关键词（1-2 个） + 情绪词 / 价值点（如影响民生 / 关键进展 / 应对指南）
    - 字数：18-25 字，手机端显示完整，避免 “今日新闻汇总” 类平淡表述
    - 风格：简洁有力，带正向引导，突出 “实用性” 或 “突破性”，可适当用 “！” 强化情绪
2.  生成 1 个开头文案字段 "opening"：
    - 风格：干练实用，符合早间快速读新闻的节奏，30-45 字（不包含追加句）
    - 内容：时间提醒 + 1-2 个核心新闻钩子（绑定民生 / 利益点）+ 阅读引导（点明高效）+ 追加句 “先上汇总图！15 个热点标题 + 两句话精华都在这，刷完图再看详析，不浪费你一秒钟”，自然衔接不生硬
    - 点缀：可加 1 个提示类 emoji（如📌/⏰），整体突出 “省时间、有价值”
3.  生成 1 个结尾文案字段 "ending"：
    - 风格：温和正向，带引导性，40-55字
    - 内容：总结价值+互动提问（可选）+关注引导
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
            model=_get_env("MODEL_NAME", MODEL_NAME),
            messages=[
                {"role": "system", "content": "你是一位资深新闻编辑与评论员，擅长用简短两句话给出讲解与观点。"},
                {"role": "user", "content": prompt}
            ],
            stream=False
        )
        
        result = response.choices[0].message.content
        result = _clean_json_text(result)
        
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

async def format_with_llm(data):
    client = _make_openai_client()
    if not client:
        print("错误：未安装 openai 库。")
        return None
    payload = {
        "page_title": data.get("page_title", ""),
        "opening": data.get("opening", ""),
        "ending": data.get("ending", ""),
        "quote": data.get("quote", {}),
        "news_items": data.get("news_items", [])[:15]
    }

    prompt = f"""
请把下面结构化内容排版为 Markdown，风格为“60s 轻读手记”早报。
严格要求：
1. 只输出 Markdown，不要输出多余解释文字。
2. 第一行使用一级标题，格式为：# {payload["page_title"]}。
3. 第二行是干练引导语，包含 emoji，并提醒“先上汇总图”，语气权威实用，格式：> 内容。
4. 新闻标题使用二级标题格式：## 🔸 序号. 新闻标题。
5. 每条新闻标题下一行输出 1 个关键词标签（如 #财经），再下一行使用正文格式，正文基于 summary_long，可适度润色但不添加新事实。
6. 保持 news_items 的原始顺序输出，每条新闻之间空一行增强可读性。
7. 结尾用分割线 --- ，下一行输出结尾文案，不要添加“今日互动”字样。
8. 再下一行输出金句（来自 quote.text），可省略作者，不要额外引号。
9. 仅在每条新闻之间添加一行空行，其余位置不出现空白行。

内容：
{json.dumps(payload, ensure_ascii=False)}
"""

    print("正在请求 AI 进行 Markdown 排版...")
    try:
        response = client.chat.completions.create(
            model=_get_env("MODEL_NAME", MODEL_NAME),
            messages=[
                {"role": "system", "content": "你是一位资深新媒体编辑，擅长公众号早报排版与信息密度控制。"},
                {"role": "user", "content": prompt}
            ],
            stream=False
        )
        result = response.choices[0].message.content
        return result.strip()
    except Exception as e:
        print(f"Markdown 排版失败: {e}")
        return None

def _safe_filename(value):
    value = (value or "").strip()
    if not value:
        return "untitled"
    value = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:80] if len(value) > 80 else value

async def fetch_tophub_hot_text(url):
    source_auto = {"name": "Tophub Hot", "url": url, "type": "auto", "selector": "body"}
    content = await fetch_html_content(source_auto)
    if content and len(content) > 500:
        return content
    source_manual = {"name": "Tophub Hot (manual)", "url": url, "type": "manual_captcha", "selector": "body"}
    return await fetch_html_content(source_manual)

async def _extract_douyin_video_cards(page, max_items):
    return await page.evaluate(
        """
        (maxItems) => {
          const anchors = Array.from(document.querySelectorAll('a[href*="/video/"]'));
          const seen = new Set();
          const results = [];
          for (const a of anchors) {
            const raw = a.getAttribute('href') || '';
            const href = (a.href || raw || '').trim();
            if (!href) continue;
            let url = href;
            try {
              url = new URL(href, location.origin).toString();
            } catch (e) {}
            if (!url.includes('/video/')) continue;
            if (seen.has(url)) continue;
            const text = (a.innerText || '').replace(/\\s+/g, ' ').trim();
            const container = a.closest('div') || a.parentElement || a;
            const snippet = (container && container.innerText ? container.innerText : '').replace(/\\s+/g, ' ').trim();
            const title = (text || snippet || '').slice(0, 120);
            results.push({ url, title, snippet: snippet.slice(0, 240) });
            seen.add(url);
            if (results.length >= maxItems) break;
          }
          return results;
        }
        """,
        max_items,
    )

async def fetch_douyin_search_videos(keyword, max_items=3, manual=True):
    search_url = f"https://www.douyin.com/search/{urllib.parse.quote(keyword)}"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        page = await context.new_page()
        try:
            await page.goto(search_url, timeout=60000)
            await page.wait_for_timeout(4000)
            try:
                await page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass

            body_text = ""
            try:
                body_text = await page.inner_text("body")
            except Exception:
                body_text = ""
            body_text_l = body_text.lower()
            need_manual = (
                len(body_text) < 400
                or "验证码" in body_text
                or "安全验证" in body_text
                or "verify" in body_text_l
                or "just a moment" in body_text_l
                or "captcha" in body_text_l
            )
            if manual and need_manual:
                print("\n" + "=" * 50)
                print("【需要人工介入】抖音可能触发验证/登录。")
                print(f"已打开：{search_url}")
                print("=" * 50 + "\n")
                await asyncio.to_thread(input, ">> 请在浏览器里完成验证并进入搜索结果页后，按【回车键】继续...")
                await page.wait_for_timeout(2000)

            cards = await _extract_douyin_video_cards(page, max_items=max_items)
            normalized = []
            for c in cards or []:
                url = (c.get("url") or "").strip()
                if not url:
                    continue
                normalized.append(
                    {
                        "url": url,
                        "title": (c.get("title") or "").strip(),
                        "snippet": (c.get("snippet") or "").strip(),
                    }
                )
            return normalized[:max_items]
        finally:
            await browser.close()

async def microtoutiao_analyze_with_llm(payload):
    client = _make_openai_client()
    if not client:
        print("错误：未安装 openai 库。")
        return None

    prompt = f"""
你是一位短视频运营专家 + 今日头条微头条写作教练。请基于我提供的“TopHub热点榜单 + 抖音搜索结果卡片信息”，做选题决策与可执行拆解。

硬性要求：
1) 只允许使用输入中出现的信息，不能凭空编造具体剧情、人物经历、视频画面与音频细节。
2) 如果某项信息无法从输入判断，必须输出“未知”，并给出“需在看过视频后验证的检查点”。
3) 输出必须是纯 JSON，不能包含 Markdown、解释文字、代码块标记。

目标：
- 从候选热点里挑 1 个最适合做微头条的核心关键词；
- 从对应的抖音视频卡片里挑 1-2 条作为“素材入口”（如果没有也要说明原因并给出替代策略）；
- 产出：关键词矩阵、情绪/争议点、二创建议、合规风险提示、以及文章写作计划（标题备选+五段式要点）。

输出 JSON Schema（必须严格遵守字段名）：
{{
  "chosen_keyword": "string",
  "chosen_reason": "string",
  "chosen_videos": [
    {{"url":"string","title":"string","why":"string"}}
  ],
  "unknown_checkpoints": ["string"],
  "framework": {{
    "text_layer": "string",
    "visual_layer": "string",
    "audio_layer": "string",
    "interaction_layer": "string",
    "traffic_layer": "string"
  }},
  "burst_keywords": {{
    "sensitivity": "普通|热门|现象级",
    "controversy": "普通|热门|现象级",
    "virality": "普通|热门|现象级",
    "phrases": ["string"],
    "templates": ["string"]
  }},
  "keyword_matrix": [
    {{"theme":"string","keywords":["string"]}}
  ],
  "comments_emotion": {{
    "likely_emotions": ["string"],
    "possible_conflicts": ["string"]
  }},
  "secondary_creation": {{
    "angles": ["string"],
    "cta_questions": ["string"]
  }},
  "compliance": {{
    "risk_points": ["string"],
    "safe_wording": ["string"]
  }},
  "article_plan": {{
    "audience": "string",
    "titles": ["string","string","string"],
    "outline": [
      {{"part":"hook","notes":"string"}},
      {{"part":"pain","notes":"string"}},
      {{"part":"reveal","notes":"string"}},
      {{"part":"climax","notes":"string"}},
      {{"part":"ending","notes":"string"}}
    ],
    "hashtags": ["string"]
  }}
}}

输入数据：
{json.dumps(payload, ensure_ascii=False)}
"""

    print("正在请求 AI 进行选题与素材分析...")
    response = client.chat.completions.create(
        model=_get_env("MODEL_NAME", MODEL_NAME),
        messages=[
            {"role": "system", "content": "你擅长热点选题、短视频拆解与微头条写作。"},
            {"role": "user", "content": prompt},
        ],
        stream=False,
    )
    raw = response.choices[0].message.content
    cleaned = _clean_json_text(raw)
    try:
        return json.loads(cleaned)
    except Exception as e:
        print(f"微头条分析 JSON 解析失败: {e}")
        return None

async def microtoutiao_write_with_llm(analysis_json):
    client = _make_openai_client()
    if not client:
        print("错误：未安装 openai 库。")
        return None

    keyword = (analysis_json or {}).get("chosen_keyword") or ""
    prompt = f"""
你是一位拥有10年经验的新媒体运营总监，同时是今日头条百万粉丝账号御用写手。请根据我提供的“选题分析 JSON”，写一篇可直接发布的微头条长文。

硬性要求：
1) 只允许基于 analysis_json 中给出的事实/判断写作；不能编造具体人物姓名、机构内幕、精确数据与时间点。
2) 允许使用“某研究院/业内人士/不少人/很多家庭”等模糊化表达，但必须避免绝对化口吻（例如“研究表明”“专家指出”）。
3) 禁止出现“作为一名AI”相关表述。
4) 结构使用黄金五段式：hook/pain/reveal/climax/ending。
5) 总长度 1800-2500 字。
6) 标题三选一：从 analysis_json.article_plan.titles 中选 1 个做最终标题，并保证标题前 10 个字包含核心关键词“{keyword}”（若不满足请微调但不改变含义）。
7) 结尾必须用问句引导评论；并在文末输出 3-6 个 hashtags（从 analysis_json.article_plan.hashtags 选，必要时可少量微调）。
8) 只输出 Markdown：第一行是标题（# 标题），正文为普通段落；不要输出任何额外说明。

analysis_json：
{json.dumps(analysis_json, ensure_ascii=False)}
"""

    print("正在请求 AI 生成微头条文章...")
    response = client.chat.completions.create(
        model=_get_env("MODEL_NAME", MODEL_NAME),
        messages=[
            {"role": "system", "content": "你擅长把热点写成高转发微头条。"},
            {"role": "user", "content": prompt},
        ],
        stream=False,
    )
    return (response.choices[0].message.content or "").strip()

async def run_microtoutiao(args):
    tophub_text = await fetch_tophub_hot_text(args.tophub_url)
    if not tophub_text:
        print("TopHub 热榜抓取失败。")
        return

    hot_items = build_hot_items(tophub_text, max_items=max(args.max_hot_items, args.candidate_keywords))
    if not hot_items:
        print("TopHub 热榜解析失败。")
        return

    hot_items = hot_items[: args.max_hot_items]
    candidate = [it["title"] for it in hot_items[: args.candidate_keywords] if it.get("title")]
    candidate = [c.strip() for c in candidate if c and len(c.strip()) >= 2]
    if not candidate:
        print("未能从热榜中提取候选关键词。")
        return

    douyin_map = {}
    for kw in candidate:
        print(f"抖音搜索：{kw}")
        try:
            videos = await fetch_douyin_search_videos(kw, max_items=args.videos_per_keyword, manual=args.manual_douyin)
        except Exception as e:
            print(f"抖音搜索失败：{kw}，错误: {e}")
            videos = []
        douyin_map[kw] = videos

    payload = {
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "tophub_url": args.tophub_url,
        "hot_items": hot_items,
        "douyin_search_results": douyin_map,
        "constraints": {"audience": "30-55岁三四线城市用户为主"},
    }
    analysis = await microtoutiao_analyze_with_llm(payload)
    if not analysis:
        print("微头条分析失败。")
        return

    article_md = await microtoutiao_write_with_llm(analysis)
    if not article_md:
        print("微头条生成失败。")
        return

    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    date_str = datetime.datetime.now().strftime("%Y年%m月%d日")
    chosen_kw = _safe_filename(analysis.get("chosen_keyword") or "热点")
    out_path = os.path.join(out_dir, f"微头条_{date_str}_{chosen_kw}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(article_md.strip() + "\n")
    print(f"微头条已生成: {out_path}")

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
        if isinstance(new_data.get("markdown"), str):
            current_data["markdown"] = new_data["markdown"].strip()

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
    markdown = await format_with_llm(data_processed)
    if markdown:
        data_processed["markdown"] = markdown

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
                filename = f"{date_str}.md"
                article_dir = r"d:\zixun\每日文章"
                if not os.path.exists(article_dir):
                    os.makedirs(article_dir)
                
                article_path = os.path.join(article_dir, filename)
                
                article_content = (data.get("markdown") or "").strip()
                if not article_content:
                    page_title = data.get("page_title") or f"{date_str} 今日速览"
                    article_content = f"# {page_title}\n"
                    opening = (data.get("opening") or "").strip()
                    if opening:
                        article_content += f"> {opening}\n"
                    total_items = len(data['news_items'])
                    for i, item in enumerate(data['news_items'], 1):
                        article_content += f"## 🔸 {i}. {item['title']}\n"
                        title = (item.get("title") or "").strip()
                        keyword = title[:4] if len(title) >= 2 else "要闻"
                        article_content += f"#{keyword}\n"
                        summary_long = (item.get("summary_long") or "").strip()
                        if summary_long:
                            article_content += f"### {summary_long}\n"
                            if i < total_items:
                                article_content += "\n"
                    ending = (data.get("ending") or "").strip()
                    if ending:
                        article_content += f"---\n{ending}\n"
                    quote_text = (data.get("quote") or {}).get("text") or ""
                    if quote_text:
                        article_content += f"{quote_text}\n"
                
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
    parser = argparse.ArgumentParser(add_help=True)
    subparsers = parser.add_subparsers(dest="cmd")
    subparsers.required = False

    p_daily = subparsers.add_parser("daily")

    p_micro = subparsers.add_parser("micro")
    p_micro.add_argument("--tophub-url", default="https://tophub.today/n/Dgey31RvZq")
    p_micro.add_argument("--max-hot-items", type=int, default=30)
    p_micro.add_argument("--candidate-keywords", type=int, default=6)
    p_micro.add_argument("--videos-per-keyword", type=int, default=3)
    p_micro.add_argument("--no-manual-douyin", dest="manual_douyin", action="store_false", default=True)
    p_micro.add_argument("--out-dir", default=r"d:\zixun\每日文章")

    args = parser.parse_args()

    if args.cmd is None or args.cmd == "daily":
        asyncio.run(main())
        sys.exit(0)

    if args.cmd == "micro":
        asyncio.run(run_microtoutiao(args))
        sys.exit(0)

    parser.print_help()
