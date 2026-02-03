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


def call_llm_chat(messages, model, temperature=0.2):
    api_key = _env("API_KEY", "ms-b8df244e-aa5e-4392-b3bf-4b0e0f80c052")
    api_base_url = _env("API_BASE_URL", "https://api-inference.modelscope.cn/v1")
    model = "ZhipuAI/GLM-4.7"
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


def analyze_hot_and_videos(payload, model):
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


def write_microtoutiao(analysis_json, model):
    keyword = (analysis_json or {}).get("chosen_keyword") or ""
    prompt = f"""
你是一位拥有10年经验的新媒体运营总监，同时是今日头条百万粉丝账号御用写手。请根据我提供的“选题分析 JSON”，写一篇可直接发布的微头条长文。

硬性要求：
1) 只允许基于 analysis_json 中给出的事实/判断写作；不能编造具体人物姓名、机构内幕、精确数据与时间点。
2) 允许使用“某研究院/业内人士/不少人/很多家庭”等模糊化表达，但必须避免绝对化口吻（例如“研究表明”“专家指出”）。
3) 禁止出现“作为一名AI”相关表述。
4) 结构使用黄金五段式：hook/pain/reveal/climax/ending。
5) 总长度 180-400 字。
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
    model = _env("MODEL_NAME", "ZhipuAI/GLM-4.7")
    tophub_text = await fetch_tophub_hot(args.tophub_url)
    hot_items = parse_tophub_hot_items(tophub_text, max_items=max(args.max_hot_items, args.candidate_keywords))
    if not hot_items:
        raise RuntimeError("TopHub 热榜解析失败")

    hot_items = hot_items[: args.max_hot_items]
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
    article_md = write_microtoutiao(analysis, model=model).strip()
    if not article_md:
        raise RuntimeError("生成内容为空")

    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    date_str = datetime.datetime.now().strftime("%Y年%m月%d日")
    chosen_kw = _safe_filename(analysis.get("chosen_keyword") or "热点")
    out_path = os.path.join(out_dir, f"微头条_{date_str}_{chosen_kw}.md")
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
    p_run.add_argument("--videos-per-keyword", type=int, default=3)
    p_run.add_argument("--no-manual-douyin", action="store_true", default=False)
    p_run.add_argument("--out-dir", default=r"d:\zixun\生活")
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
