import csv
import html
import random
import sys
import time
import webbrowser
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).parent
KEYWORDS_FILE = BASE_DIR / "keywords.csv"
RESULTS_FILE = BASE_DIR / "results.csv"
SERP_LOG_FILE = BASE_DIR / "serp_log.csv"
DASHBOARD_FILE = BASE_DIR / "dashboard.html"
PROFILE_DIR = BASE_DIR / ".chrome-profile"

OUT_RANK = 11  # 圏外をグラフ上で表す仮の順位（1ページ目=10位までなので11扱い）

# 1回の実行でチェックする最大キーワード数（少量ペースでブロック回避）
MAX_PER_RUN = 5

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)

STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"


class BlockedError(Exception):
    pass


def normalize(url):
    url = url.strip().lower()
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    if url.startswith("www."):
        url = url[4:]
    return url.rstrip("/")


def load_keywords():
    rows = []
    with open(KEYWORDS_FILE, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 2 or row[0].strip() in ("", "keyword") or row[0].startswith("#"):
                continue
            rows.append((row[0].strip(), row[1].strip()))
    return rows


def handle_consent(page):
    for sel in ("#L2AGLb", "button:has-text('すべて同意')", "button:has-text('同意する')"):
        try:
            if page.locator(sel).count() > 0:
                page.locator(sel).first.click(timeout=3000)
                page.wait_for_timeout(1000)
                return
        except Exception:
            pass


def fetch_top10(page, keyword):
    url = f"https://www.google.com/search?q={quote_plus(keyword)}&num=10&hl=ja&gl=jp"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    handle_consent(page)
    if "/sorry/" in page.url or page.locator("form#captcha-form").count() > 0:
        raise BlockedError()
    page.wait_for_selector("#search", timeout=15000)
    hrefs = page.eval_on_selector_all("#search a:has(h3)", "els => els.map(e => e.href)")
    results = []
    for href in hrefs:
        if not href.startswith("http"):
            continue
        host = href.split("/")[2]
        if "google.com" in host or "google.co.jp" in host:
            continue
        if href not in results:
            results.append(href)
    return results[:10]


def append_csv(path, header, rows):
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(header)
        writer.writerows(rows)


def read_csv_rows(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_series(results_rows):
    # keyword -> {url, series:[(date, rank_or_None)]}
    data = {}
    for row in results_rows:
        kw = row["keyword"]
        rank = row["rank"]
        rank_val = None if rank == "圏外" else int(rank)
        data.setdefault(kw, {"url": row["target_url"], "points": {}})
        data[kw]["url"] = row["target_url"]
        data[kw]["points"][row["date"]] = rank_val  # 同日に複数回実行したら最後を採用
    for kw in data:
        data[kw]["series"] = sorted(data[kw]["points"].items())
    return data


def latest_serp(serp_rows):
    # keyword -> [(rank, url)]（最新日付の1ページ目）
    by_kw = {}
    for row in serp_rows:
        by_kw.setdefault(row["keyword"], {})
        by_kw[row["keyword"]].setdefault(row["date"], [])
        by_kw[row["keyword"]][row["date"]].append((int(row["rank"]), row["url"]))
    result = {}
    for kw, dates in by_kw.items():
        latest_date = max(dates)
        result[kw] = sorted(dates[latest_date])
    return result


def sparkline(series, width=240, height=54):
    if len(series) < 2:
        return "<div class='nodata'>2回以上チェックすると推移グラフが出るよ</div>"
    ranks = [r if r is not None else OUT_RANK for _, r in series]
    max_r = max(max(ranks), 10)
    n = len(ranks)
    pad = 6
    coords = []
    for i, r in enumerate(ranks):
        x = pad + i / (n - 1) * (width - 2 * pad)
        y = pad + (r - 1) / (max_r - 1) * (height - 2 * pad)  # 1位が上
        coords.append((x, y))
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
                    for i, (x, y) in enumerate(coords))
    dots = "".join(
        f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3' class='{'out' if ranks[i] >= OUT_RANK else 'in'}'/>"
        for i, (x, y) in enumerate(coords))
    return (f"<svg viewBox='0 0 {width} {height}' class='spark'>"
            f"<path d='{path}' fill='none'/>{dots}</svg>")


def rank_badge(series):
    if not series:
        return "<span class='badge new'>データなし</span>", "—"
    current = series[-1][1]
    cur_label = "圏外" if current is None else f"{current}位"
    if len(series) < 2:
        return "<span class='badge new'>NEW</span>", cur_label
    prev = series[-2][1]
    cur_v = current if current is not None else OUT_RANK
    prev_v = prev if prev is not None else OUT_RANK
    if cur_v < prev_v:
        return f"<span class='badge up'>▲ {prev_v - cur_v}ランクUP</span>", cur_label
    if cur_v > prev_v:
        return f"<span class='badge down'>▼ {cur_v - prev_v}ランクDOWN</span>", cur_label
    return "<span class='badge flat'>±0 変わらず</span>", cur_label


def esc(s):
    return html.escape(str(s))


def generate_dashboard():
    results_rows = read_csv_rows(RESULTS_FILE)
    serp_rows = read_csv_rows(SERP_LOG_FILE)
    data = build_series(results_rows)
    serp = latest_serp(serp_rows)
    all_dates = sorted({row["date"] for row in results_rows})
    last_updated = all_dates[-1] if all_dates else "—"

    cards = []
    for kw in sorted(data):
        series = data[kw]["series"]
        url = data[kw]["url"]
        badge, cur_label = rank_badge(series)
        cur = series[-1][1] if series else None
        state = "out" if cur is None else ("top3" if cur <= 3 else "in")
        big = "圏外" if cur is None else str(cur)
        unit = "" if cur is None else "<span class='unit'>位</span>"

        rivals = serp.get(kw, [])
        rival_rows = "".join(
            f"<tr class='{'me' if normalize(u) == normalize(url) else ''}'>"
            f"<td class='rk'>{r}</td><td class='ru'>{esc(u)}</td></tr>"
            for r, u in rivals)
        rivals_html = (f"<details><summary>1ページ目のライバル（{len(rivals)}件）</summary>"
                       f"<table class='rivals'>{rival_rows}</table></details>"
                       if rivals else "")

        cards.append(f"""
        <div class="card {state}">
          <div class="kw">{esc(kw)}</div>
          <div class="url">{esc(url)}</div>
          <div class="rankrow">
            <div class="big">{big}{unit}</div>
            {badge}
          </div>
          {sparkline(series)}
          {rivals_html}
        </div>""")

    checked = len(data)
    ranked = sum(1 for kw in data if data[kw]["series"] and data[kw]["series"][-1][1] is not None)
    doc = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>順位チェック ダッシュボード</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Hiragino Sans", sans-serif; margin: 0;
         background: #f4f6fb; color: #1a1f36; padding: 28px 20px 60px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #6b7280; font-size: 13px; margin-bottom: 24px; }}
  .stats {{ display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; }}
  .stat {{ background: #fff; border-radius: 12px; padding: 14px 20px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .stat .n {{ font-size: 26px; font-weight: 700; }}
  .stat .l {{ font-size: 12px; color: #6b7280; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }}
  .card {{ background: #fff; border-radius: 14px; padding: 18px 20px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); border-left: 5px solid #d1d5db; }}
  .card.top3 {{ border-left-color: #16a34a; }}
  .card.in {{ border-left-color: #2563eb; }}
  .card.out {{ border-left-color: #dc2626; }}
  .kw {{ font-size: 17px; font-weight: 700; }}
  .url {{ font-size: 11px; color: #9ca3af; margin-bottom: 12px; word-break: break-all; }}
  .rankrow {{ display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }}
  .big {{ font-size: 40px; font-weight: 800; line-height: 1; }}
  .card.out .big {{ font-size: 26px; color: #dc2626; }}
  .unit {{ font-size: 15px; font-weight: 500; margin-left: 2px; }}
  .badge {{ font-size: 12px; font-weight: 600; padding: 4px 9px; border-radius: 999px; white-space: nowrap; }}
  .badge.up {{ background: #dcfce7; color: #16a34a; }}
  .badge.down {{ background: #fee2e2; color: #dc2626; }}
  .badge.flat {{ background: #f3f4f6; color: #6b7280; }}
  .badge.new {{ background: #dbeafe; color: #2563eb; }}
  .spark {{ width: 100%; height: 54px; margin-top: 12px; }}
  .spark path {{ stroke: #2563eb; stroke-width: 2.5; }}
  .spark circle.in {{ fill: #2563eb; }}
  .spark circle.out {{ fill: #dc2626; }}
  .nodata {{ font-size: 12px; color: #9ca3af; margin-top: 14px; }}
  details {{ margin-top: 12px; font-size: 12px; }}
  summary {{ cursor: pointer; color: #6b7280; }}
  table.rivals {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  table.rivals td {{ padding: 3px 4px; border-bottom: 1px solid #f0f0f0; }}
  table.rivals .rk {{ width: 24px; color: #9ca3af; text-align: right; }}
  table.rivals .ru {{ word-break: break-all; color: #4b5563; }}
  table.rivals tr.me {{ background: #eff6ff; font-weight: 600; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #0f1420; color: #e5e7eb; }}
    .stat, .card {{ background: #1a2130; box-shadow: none; }}
    .url {{ color: #6b7280; }}
    table.rivals tr.me {{ background: #1e293b; }}
    table.rivals td {{ border-bottom-color: #252d3d; }}
  }}
</style></head><body>
  <h1>📊 検索順位ダッシュボード</h1>
  <div class="sub">最終チェック日: {last_updated}</div>
  <div class="stats">
    <div class="stat"><div class="n">{checked}</div><div class="l">追跡キーワード</div></div>
    <div class="stat"><div class="n">{ranked}</div><div class="l">1ページ目内</div></div>
    <div class="stat"><div class="n">{checked - ranked}</div><div class="l">圏外</div></div>
  </div>
  <div class="grid">{"".join(cards)}</div>
</body></html>"""
    DASHBOARD_FILE.write_text(doc, encoding="utf-8")


def main():
    visible = "--visible" in sys.argv
    keywords = load_keywords()
    if not keywords:
        print("keywords.csv にキーワードがありません")
        return
    total = len(keywords)
    if total > MAX_PER_RUN:
        print(f"※ ブロック回避のため今回は先頭{MAX_PER_RUN}件だけチェックします"
              f"（残り{total - MAX_PER_RUN}件は次回以降）")
        keywords = keywords[:MAX_PER_RUN]
    today = date.today().isoformat()
    print(f"{len(keywords)}個のキーワードをチェックします（{today}）")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=not visible,
            locale="ja-JP",
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        context.add_init_script(STEALTH_JS)
        page = context.pages[0] if context.pages else context.new_page()

        for i, (keyword, target) in enumerate(keywords):
            if i > 0:
                wait = random.uniform(15, 30)
                print(f"  （ブロック回避のため {wait:.0f}秒 待機中…）")
                time.sleep(wait)
            try:
                top10 = fetch_top10(page, keyword)
            except BlockedError:
                print(f"⚠️ Googleに検知されました（CAPTCHA）。今日はここで中断します。")
                print(f"   数時間おいてから ./run.sh --visible で再開してください。")
                break
            except Exception as e:
                print(f"⚠️ 「{keyword}」の取得に失敗: {e}")
                continue

            rank = "圏外"
            target_n = normalize(target)
            for idx, url in enumerate(top10, 1):
                if normalize(url) == target_n:
                    rank = idx
                    break

            append_csv(RESULTS_FILE, ["date", "keyword", "target_url", "rank"],
                       [[today, keyword, target, rank]])
            append_csv(SERP_LOG_FILE, ["date", "keyword", "rank", "url"],
                       [[today, keyword, idx, url] for idx, url in enumerate(top10, 1)])
            mark = "✅" if rank != "圏外" else "❌"
            print(f"{mark} {keyword}: {rank}{'位' if rank != '圏外' else ''}")

        context.close()

    generate_dashboard()
    print(f"\n結果を記録して、dashboard.html を更新したよ")
    if visible:
        webbrowser.open(DASHBOARD_FILE.as_uri())


if __name__ == "__main__":
    if "--dashboard-only" in sys.argv:
        generate_dashboard()
        webbrowser.open(DASHBOARD_FILE.as_uri())
    else:
        main()
