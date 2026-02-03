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
DOUYIN_VIDEO_URL = "https://v.douyin.com/rfX-woUJtcs/"


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


def _write_json(path, data_obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data_obj, f, ensure_ascii=False, indent=2)


def _clip_text(text, limit=12000):
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[:limit]


async def fetch_page_inner_text(url, manual=False, min_len=500, wait_ms=4000):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not manual)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        page = await context.new_page()
        try:
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
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
                print("\n" + "=" * 50, flush=True)
                print("【需要人工介入】页面可能触发验证。", flush=True)
                print(f"已打开：{url}", flush=True)
                print("=" * 50 + "\n", flush=True)
                if sys.stdin and sys.stdin.isatty():
                    await asyncio.to_thread(input, ">> 完成验证并看到内容后，按【回车键】继续...")
                await page.wait_for_timeout(2000)
                try:
                    text = await page.inner_text("body")
                except Exception:
                    text = ""
            return text
        finally:
            await browser.close()


async def fetch_douyin_video_page_text(video_url, allow_manual=True):
    text = await fetch_page_inner_text(video_url, manual=False, min_len=400)
    if text and len(text) >= 400:
        return text
    if allow_manual:
        return await fetch_page_inner_text(video_url, manual=True, min_len=400)
    return text or ""


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


def analyze_douyin_video(payload, model):
    prompt = f"""
你是一位短视频运营专家 + 今日头条微头条写作教练。请基于我提供的“抖音视频链接 + 页面文本抓取结果”，做视频内容归纳与可执行拆解，并输出一份可直接用于写作的结构化 JSON。
视频分析逻辑：
1.文本层:标题、文案、字幕、口播稿
2.视觉层:出镜人物、场景符号、动作设计、画面冲突点
3.听觉层:BGM热门指数、音效记忆点
4.互动层:评论区热词云、用户二创倾向、争议焦点
5.流量层:疑似DOU+投放点、完播率钩子位置
[爆点关键词三性原则]
-敏感性:是否触及社会敏感神经(婚恋、贫富、教育)
-争议性:是否支持/反对两派明显
-病毒:是否易改编、易模仿、易传播
请提取的不仅是词，而是爆点词组和句式模板，并标注:
-热度等级:(普通)/(热门)/(现象级)
-生命周期:昙花一现/季度热词/年度梗
-适用赛道:美妆/美食/知识/剧情/带货..
最终输出请包含:关键词矩阵+评论区情绪分析报告+二次创作建议。
硬性要求：
1) 只允许使用输入中出现的信息，不能凭空编造具体剧情、人物经历、视频画面与音频细节。
2) 如果某项信息无法从输入判断，必须输出“未知”，并给出“需在看过视频后验证的检查点”。
3) 输出必须是纯 JSON，不能包含 Markdown、解释文字、代码块标记。

目标：
1) 以输入 JSON 中的 video_url 指向的视频为唯一素材入口，不允许另起炉灶换题。
2) 在无法确定事实时，用“未知”并输出 unknown_checkpoints，方便我后续人工核验。
3) 产出：关键词矩阵、情绪/争议点、二创建议、合规风险提示、以及文章写作计划（标题备选+更细的五段式要点）。

写作计划要求：
1) titles 必须输出 3 个不同角度标题，且每个标题前 10 个字包含 chosen_keyword。
2) outline 的每个 part.notes 必须写得“可直接照着写”，至少 80 字，包含：场景/人物/冲突/情绪/一句可加粗的金句草稿。


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
你是一位拥有10年经验的新媒体运营总监，同时是今日头条百万粉丝账号御用写手。你精通爆款文章的底层逻辑，深谙人性痛点与流量密码。请根据我提供的“选题分析 JSON”，写一篇更丰富、更完整、可直接发布的文章。

硬性要求：
1) 只允许基于 analysis_json 中给出的事实/判断写作；不能编造具体人物姓名、机构内幕、精确数据与时间点。
2) 允许使用“某研究院/业内人士/不少人/很多家庭”等模糊化表达，但必须避免绝对化口吻（例如“研究表明”“专家指出”）。
3) 禁止出现“作为一名AI”相关表述。
4) 结构使用黄金五段式：hook / pain / reveal / climax / ending，但不要用“小标题：第一段/第二段”这类显眼标签。
5) 正文长度（不含标题）必须在 {min_len}-{max_len} 字，不能偏短。
6) 必须包含 2-3 句可加粗的爆款金句（用 Markdown **加粗**）。
7) 结尾必须用问句引导评论；并在文末输出 3-6 个 hashtags（从 analysis_json.article_plan.hashtags 选，必要时可少量微调）。
8) 只输出 Markdown：第一行是标题（# 标题），正文为普通段落；不要输出任何额外说明或统计信息。

-目标受众:30-55岁三四线城市用户为主
文章字数:1200-2000字
输出要求:
一、标题生成(三选一)
必须严格遵循”热点+悬念+标签"三位一体原则，生成3个备选标题:
标题黄金公式库(任选其三):
1.数字冲击型:《XX关键词背后:73%家庭正面临的3个致命陷阱》
2.反常识型:《别再盯着”XX关键词”了，真正可怕的是第4条新规》
3.身份代入型:《有“XX关键词的80后，注是逃不世这些种结局
4.悬念设问型:《"XX关键词”上热搜第3天，我收到了银行内部短信》
5.地域绑定型:《我们小县城的”XX关键词”，比北上广残酷10倍》
标题违禁词分级警示:
安全区:惊现、揭秘、万万没想到、扎心、崩溃
慎用区(可能限流):骗局、黑幕、曝光、丧尽天良
-红线区(直接封号):官商勾结、灭门、带血、暗箱操作
必须满足的硬性指标:
前10个字必须出现核心关键词(提升搜索权重)
必须包含1个具体数字或极端对比词
-必须埋设”信息差”(内幕/独家/泄密)
-总字数严格控制在22-28字(过长折叠，过短无信息量)
平台潜规则:
-同一热点主题，避开前100篇同质标题角度(算法削权重)首次推荐点击率需>3%，否则流量悬崖式下跌在标题中植入地域词(如"我们小县城”、“河南老乡”)可提升18%点击率

正文结构(黄金五段式)
第一段:钩子开头(150字内)
禁止用"首先、近年来、随着"等A词汇
必须以一个真实故事、极端案例或反常识观点开场前3句必须埋设悬念或冲突
使用"你绝对想不到”、"就在上周”、“我邻居"等强代入感表述
第二段:痛点放大(300字)
用2-3个具体生活化案例引发共鸣
每个案例配一句金句(加粗)
使用短句、反问、排比句式关键词自然植入3-5次
第三段:揭秘/反转(600字)
提供至少3个"不为人知"的干货点
用"内幕消息”、“独家调查”等包装专业性
每个观点必须附带:
具体数据或时间(如:2024年7月、73%)
人物故事(如:我同学、小区门口的王姐)
关键词密度保持在4%-6%
第四段:情绪高潮(400字)
制造强烈价值观冲突或道德评判
使用"说白了”、“戳穿一个真相"等口语化转折
必须包含1-2句能单独转发的”爆款金句”
段落不超过3行，多用感叹号
第五段:互动结尾(100字)
禁止用"总之、综上所述”必须以问句结尾引导评论提供争议性话题点
示例:"你怎么看?欢迎在评论区骂醒我..”
三、风格铁律
绝对禁止:
使用"首先、其次、最后"等逻辑词
出现”作为一名AI”相关表述
绝对化术语(“研究表明”、“专家指出”)不带具体人名
超过15字的长句超过3处
说教口吻，要高用户一等的感觉
四、原创性保障
所有案例必须虚构但符合生活逻辑数据必须模糊化处理但显得真实(如:73.6%、某研究院)加入个人经历(“我二舅”、“我老婆”)增加可信度关键词必须自然融入，禁止堆砌

进阶技巧:
想让文章更爆款，可在关键词后追加”-情绪化”(如:房价下跌-焦虑)想增加权威性，追加”-内幕”(如:房价下跌-银行内幕)想切入特定人群，追加”-宝妈/农民工/白领”

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
4) 正文长度（不含标题）控制在 {min_len}-{max_len} 字，不能偏短；
5) 段落短、信息密度高；至少保留 2 句 **加粗金句**；结尾必须是问句引导评论；文末保留 3-6 个 hashtags；
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


async def cmd_run(args):
    model = _env("MODEL_NAME", MODEL_NAME)
    video_url = (args.douyin_url or "").strip() or (DOUYIN_VIDEO_URL or "").strip()
    if not video_url:
        raise RuntimeError("请在脚本顶部设置 DOUYIN_VIDEO_URL，或运行时传入 --douyin-url")

    run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = args.json_path
    if not json_path:
        json_path = os.path.join(args.out_dir, f"过程_{run_ts}.json")

    print(f"抓取抖音页面：{video_url}", flush=True)
    page_text = await fetch_douyin_video_page_text(video_url, allow_manual=not args.no_manual_video)
    payload = {
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "video_url": video_url,
        "video_page_text_len": len(page_text or ""),
        "video_page_text_excerpt": _clip_text(page_text, limit=args.max_page_chars),
        "constraints": {"audience": "30-55岁三四线城市用户为主"},
        "params": {
            "min_len": args.min_len,
            "max_len": args.max_len,
            "no_manual_video": args.no_manual_video,
            "max_page_chars": args.max_page_chars,
        },
    }
    _write_json(json_path, {"stage": "payload", "payload": payload})

    print("正在请求 AI 分析视频并生成写作计划...", flush=True)
    analysis = analyze_douyin_video(payload, model=model)
    _write_json(json_path, {"stage": "analysis", "payload": payload, "analysis": analysis})

    print("正在请求 AI 生成微头条文章...", flush=True)
    article_md = write_microtoutiao(analysis, model=model, min_len=args.min_len, max_len=args.max_len).strip()
    if not article_md:
        raise RuntimeError("生成内容为空")
    _write_json(
        json_path,
        {"stage": "draft", "payload": payload, "analysis": analysis, "draft_markdown": article_md},
    )

    print("正在请求 AI 进行最终排版润色...", flush=True)
    article_md = polish_article_markdown(article_md, analysis_json=analysis, model=model)
    _write_json(
        json_path,
        {"stage": "polished", "payload": payload, "analysis": analysis, "polished_markdown": article_md},
    )

    print("正在进行质量检查与最终修正...", flush=True)
    article_md = review_and_fix_article(article_md, analysis_json=analysis, model=model, min_len=args.min_len, max_len=args.max_len)

    base_out_dir = args.out_dir
    article_dir = os.path.join(base_out_dir, "文章")
    os.makedirs(article_dir, exist_ok=True)
    date_str = datetime.datetime.now().strftime("%Y年%m月%d日")
    chosen_kw = _safe_filename((analysis or {}).get("chosen_keyword") or "热点")
    out_path = os.path.join(article_dir, f"微头条_{date_str}_{chosen_kw}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(article_md + "\n")
    print(f"微头条已生成: {out_path}", flush=True)
    _write_json(
        json_path,
        {
            "stage": "final",
            "payload": payload,
            "analysis": analysis,
            "final_markdown": article_md,
            "output_markdown_path": out_path,
        },
    )
    print(f"过程 JSON 已保存: {json_path}", flush=True)


def build_parser():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--douyin-url", default="")
    parser.add_argument("--no-manual-video", action="store_true", default=False)
    parser.add_argument("--out-dir", default=r"d:\zixun\生活")
    parser.add_argument("--min-len", type=int, default=1200)
    parser.add_argument("--max-len", type=int, default=2000)
    parser.add_argument("--max-page-chars", type=int, default=12000)
    parser.add_argument("--json-path", default="")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(cmd_run(args))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
