import argparse
import asyncio
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from playwright.async_api import async_playwright


API_KEY = "ms-b8df244e-aa5e-4392-b3bf-4b0e0f80c052"
API_BASE_URL = "https://api-inference.modelscope.cn/v1"
MODEL_NAME = "ZhipuAI/GLM-4.7"


def _env(name, default_value=""):
    value = os.environ.get(name)
    if value is None:
        return default_value
    value = value.strip()
    return value if value else default_value


def _join_url(base, path):
    base = (base or "").strip()
    path = (path or "").strip()
    if not base:
        return path
    if base.endswith("/"):
        base = base[:-1]
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _clean_json_text(text):
    result = (text or "").replace("```json", "").replace("```", "").strip()
    result = re.sub(r':\s*“', ': "', result)
    result = re.sub(r'”\s*,', '",', result)
    result = re.sub(r'”\s*}', '"}', result)
    result = re.sub(r'”\s*]', '"]', result)
    return result


def _safe_filename(value):
    value = (value or "").strip()
    if not value:
        return "untitled"
    value = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:80] if len(value) > 80 else value


def parse_tophub_hot_items(text, max_items=120):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
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


async def fetch_page_inner_text(url, manual=False, min_len=500, wait_ms=4000):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not manual)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        page = await context.new_page()
        try:
            await page.goto(url, timeout=60000)
            await page.wait_for_timeout(wait_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            text = ""
            try:
                text = await page.inner_text("body")
            except Exception:
                text = ""
            text_l = text.lower()
            need_manual = (
                len(text) < min_len
                or "just a moment" in text_l
                or "verify" in text_l
                or "captcha" in text_l
                or "验证码" in text
                or "安全验证" in text
            )
            if manual and need_manual:
                print("\n" + "=" * 50)
                print("【需要人工介入】页面可能触发验证。")
                print(f"已打开：{url}")
                print("=" * 50 + "\n")
                await asyncio.to_thread(input, ">> 完成验证并看到内容后，按【回车键】继续...")
                await page.wait_for_timeout(2000)
                try:
                    text = await page.inner_text("body")
                except Exception:
                    text = ""
            return text
        finally:
            await browser.close()


async def fetch_tophub_hot(url):
    text = await fetch_page_inner_text(url, manual=False, min_len=200)
    if text and len(text) >= 200:
        return text
    return await fetch_page_inner_text(url, manual=True, min_len=200)


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
                await asyncio.to_thread(input, ">> 完成验证并进入搜索结果页后，按【回车键】继续...")
                await page.wait_for_timeout(2000)

            cards = []
            rounds = 0
            while rounds < 8 and len(cards) < max_items:
                await _scroll_to_load_more(page, rounds=1, wait_ms=900)
                cards = await _extract_douyin_video_cards(page, max_items=max_items)
                rounds += 1
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


def call_llm_chat(messages, model, temperature=0.2):
    api_key = _env("API_KEY", API_KEY)
    api_base_url = _env("API_BASE_URL", API_BASE_URL)
    if not api_key:
        raise RuntimeError("缺少 API_KEY 环境变量")

    url = _join_url(api_base_url, "/chat/completions")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise RuntimeError(f"LLM HTTPError {e.code}: {body[:1200]}") from e
    except Exception as e:
        raise RuntimeError(f"LLM 请求失败: {e}") from e

    try:
        obj = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"LLM 响应不是 JSON: {raw[:1200]}") from e
    choices = obj.get("choices") or []
    if not choices:
        raise RuntimeError(f"LLM 响应缺少 choices: {raw[:1200]}")
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content") or ""
    return str(content).strip()


def select_candidate_keywords(hot_items, candidate_count, model):
    prompt = f"""
你是今日头条微头条选题编辑。请从 TopHub 热点列表中挑选最适合做内容的 {candidate_count} 个候选关键词，尽量满足：
1) 不依赖“必须看完整视频”才能写的细节；仅凭公开信息也能做观点解读；
2) 争议适中、有讨论空间，但避免高风险敏感内容；
3) 覆盖不同话题方向，避免同质化；
4) 输出顺序：最值得写的在前。

只能使用输入的标题文本进行判断，不要编造事件细节。
输出必须是纯 JSON：
{{"candidates":["string"]}}

输入：
{json.dumps({"hot_items": hot_items[:120]}, ensure_ascii=False)}
"""
    content = call_llm_chat(
        messages=[
            {"role": "system", "content": "你擅长做热点选题与内容策划。"},
            {"role": "user", "content": prompt},
        ],
        model=model,
        temperature=0.2,
    )
    cleaned = _clean_json_text(content)
    data = json.loads(cleaned)
    candidates = data.get("candidates") or []
    if not isinstance(candidates, list):
        return []
    normalized = []
    for c in candidates:
        if not isinstance(c, str):
            continue
        s = c.strip()
        if len(s) >= 2:
            normalized.append(s)
    return normalized[:candidate_count]



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

    content = call_llm_chat(
        messages=[
            {"role": "system", "content": "你擅长热点选题、短视频拆解与微头条写作。"},
            {"role": "user", "content": prompt},
        ],
        model=model,
        temperature=0.2,
    )
    cleaned = _clean_json_text(content)
    return json.loads(cleaned)


def write_microtoutiao(analysis_json, model, min_len, max_len):
    keyword = (analysis_json or {}).get("chosen_keyword") or ""
    prompt = f"""
你是一位拥有10年经验的新媒体运营总监，同时是今日头条百万粉丝账号御用写手。请根据我提供的“选题分析 JSON”，写一篇可直接发布的微头条长文。

硬性要求：
1) 只允许基于 analysis_json 中给出的事实/判断写作；不能编造具体人物姓名、机构内幕、精确数据与时间点。
2) 允许使用“某研究院/业内人士/不少人/很多家庭”等模糊化表达，但必须避免绝对化口吻（例如“研究表明”“专家指出”）。
3) 禁止出现“作为一名AI”相关表述。
4) 结构使用黄金五段式：hook/pain/reveal/climax/ending。
5) 总长度 {min_len}-{max_len} 字。
6) 标题三选一：从 analysis_json.article_plan.titles 中选 1 个做最终标题，并保证标题前 10 个字包含核心关键词“{keyword}”（若不满足请微调但不改变含义）。
7) 结尾必须用问句引导评论；并在文末输出 3-6 个 hashtags（从 analysis_json.article_plan.hashtags 选，必要时可少量微调）。
8) 只输出 Markdown：第一行是标题（# 标题），正文为普通段落；不要输出任何额外说明。

analysis_json：
{json.dumps(analysis_json, ensure_ascii=False)}
"""
    return call_llm_chat(
        messages=[
            {"role": "system", "content": "你擅长把热点写成高转发微头条。"},
            {"role": "user", "content": prompt},
        ],
        model=model,
        temperature=0.65,
    )


def polish_article_markdown(article_md, analysis_json, model):
    prompt = f"""
你是一位资深新媒体总编。请对我提供的微头条 Markdown 进行排版与润色，要求：
1) 不添加任何新事实，不编造具体人物、机构内幕、精确数据与时间点；
2) 保留原文观点框架，但让表达更自然、更抓人；
3) 段落短一些（每段不超过3行），关键金句可加粗；
4) 保持 Markdown：第一行是 # 标题；正文用自然段；末尾保留 hashtags；
5) 不要输出任何解释，只输出最终 Markdown。

analysis_json：
{json.dumps(analysis_json, ensure_ascii=False)}

原文 Markdown：
{article_md}
"""
    return call_llm_chat(
        messages=[
            {"role": "system", "content": "你擅长把文章排版得更适合手机阅读。"},
            {"role": "user", "content": prompt},
        ],
        model=model,
        temperature=0.35,
    ).strip()


def review_and_fix_article(article_md, analysis_json, model, min_len, max_len):
    prompt = f"""
你是内容质检编辑。请检查并修正我提供的微头条 Markdown，使其更符合平台分发与合规要求。

必须满足：
1) 不新增任何事实，不编造具体人物、机构内幕、精确数据与时间点；
2) 不出现“作为一名AI”等措辞；
3) 标题前10个字包含核心关键词（analysis_json.chosen_keyword），如果不满足请修正标题；
4) 全文长度控制在 {min_len}-{max_len} 字；
5) 段落短、信息密度高；结尾必须是问句引导评论；文末保留 3-6 个 hashtags；
6) 只输出最终 Markdown，不要解释。

analysis_json：
{json.dumps(analysis_json, ensure_ascii=False)}

原文 Markdown：
{article_md}
"""
    return call_llm_chat(
        messages=[
            {"role": "system", "content": "你擅长合规检查与爆款表达优化。"},
            {"role": "user", "content": prompt},
        ],
        model=model,
        temperature=0.25,
    ).strip()


async def _scroll_to_load_more(page, rounds=6, wait_ms=1200):
    for _ in range(max(0, rounds)):
        try:
            await page.mouse.wheel(0, 2200)
        except Exception:
            pass
        await page.wait_for_timeout(wait_ms)



async def cmd_hot(args):
    text = await fetch_tophub_hot(args.tophub_url)
    items = parse_tophub_hot_items(text, max_items=args.max_items)
    for it in items[: args.max_items]:
        meta = []
        if it.get("source"):
            meta.append(it["source"])
        if it.get("heat"):
            meta.append(it["heat"])
        meta_str = f"（{'，'.join(meta)}）" if meta else ""
        print(f"{it['rank']}. {it['title']}{meta_str}")


async def cmd_run(args):
    model = _env("MODEL_NAME", MODEL_NAME)
    tophub_text = await fetch_tophub_hot(args.tophub_url)
    hot_items = parse_tophub_hot_items(tophub_text, max_items=max(args.max_hot_items, args.candidate_keywords))
    if not hot_items:
        raise RuntimeError("TopHub 热榜解析失败")

    hot_items = hot_items[: args.max_hot_items]
    try:
        candidate = select_candidate_keywords(hot_items, candidate_count=args.candidate_keywords, model=model)
    except Exception:
        candidate = []
    if not candidate:
        candidate = [it["title"] for it in hot_items[: args.candidate_keywords] if it.get("title")]
        candidate = [c.strip() for c in candidate if c and len(c.strip()) >= 2]
    if not candidate:
        raise RuntimeError("未能从热榜提取候选关键词")

    douyin_map = {}
    for kw in candidate:
        print(f"抖音搜索：{kw}")
        try:
            videos = await fetch_douyin_search_videos(kw, max_items=args.videos_per_keyword, manual=not args.no_manual_douyin)
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

    print("正在请求 AI 进行选题与素材分析...")
    analysis = analyze_hot_and_videos(payload, model=model)

    print("正在请求 AI 生成微头条文章...")
    article_md = write_microtoutiao(analysis, model=model, min_len=args.min_len, max_len=args.max_len).strip()
    if not article_md:
        raise RuntimeError("生成内容为空")

    print("正在请求 AI 进行最终排版润色...")
    article_md = polish_article_markdown(article_md, analysis_json=analysis, model=model)

    print("正在进行质量检查与最终修正...")
    article_md = review_and_fix_article(article_md, analysis_json=analysis, model=model, min_len=args.min_len, max_len=args.max_len)

    base_out_dir = args.out_dir
    article_dir = os.path.join(base_out_dir, "文章")
    os.makedirs(article_dir, exist_ok=True)
    date_str = datetime.datetime.now().strftime("%Y年%m月%d日")
    chosen_kw = _safe_filename(analysis.get("chosen_keyword") or "热点")
    out_path = os.path.join(article_dir, f"微头条_{date_str}_{chosen_kw}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(article_md + "\n")
    print(f"微头条已生成: {out_path}")


def build_parser():
    parser = argparse.ArgumentParser(add_help=True)
    subparsers = parser.add_subparsers(dest="cmd")
    subparsers.required = True

    p_hot = subparsers.add_parser("hot")
    p_hot.add_argument("--tophub-url", default="https://tophub.today/n/Dgey31RvZq")
    p_hot.add_argument("--max-items", type=int, default=30)
    p_hot.set_defaults(func=cmd_hot)

    p_run = subparsers.add_parser("run")
    p_run.add_argument("--tophub-url", default="https://tophub.today/n/Dgey31RvZq")
    p_run.add_argument("--max-hot-items", type=int, default=30)
    p_run.add_argument("--candidate-keywords", type=int, default=6)
    p_run.add_argument("--videos-per-keyword", type=int, default=6)
    p_run.add_argument("--no-manual-douyin", action="store_true", default=False)
    p_run.add_argument("--out-dir", default=r"d:\zixun\生活")
    p_run.add_argument("--min-len", type=int, default=180)
    p_run.add_argument("--max-len", type=int, default=400)
    p_run.set_defaults(func=cmd_run)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    func = getattr(args, "func", None)
    if not func:
        parser.print_help()
        return 2
    asyncio.run(func(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
