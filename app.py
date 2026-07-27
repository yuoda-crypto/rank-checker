import csv
import json
import platform
import random
import sys
import threading
import time
import webbrowser
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

import webview

IS_WIN = platform.system() == "Windows"

# PyInstallerで固めた配布版はexeの隣、開発時はこのファイルの隣が基準
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent
KEYWORDS_FILE = BASE_DIR / "keywords.csv"
RESULTS_FILE = BASE_DIR / "results.csv"
SERP_LOG_FILE = BASE_DIR / "serp_log.csv"
COMPETITORS_FILE = BASE_DIR / "competitors.csv"
UI_FILE = BASE_DIR / "ui.html"
PROFILE_DIR = BASE_DIR / ".chrome-profile"

MAX_RANK = 9  # 取得は1〜9位固定。9位以内に見つからなければ圏外
MAX_PER_RUN = 5  # 1回の実行でチェックする最大キーワード数（少量ペースでブロック回避）
MAX_COMPETITORS = 4  # グラフの見やすさと配色（4色）の上限

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
) if IS_WIN else (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)

STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

# 都道府県 → 県庁所在地のGPS座標。ブラウザの位置情報を偽装して
# Googleに「デバイスの現在地」として認識させる（uuleパラメータは2026年時点で実IPに負けて効かない）
PREF_GEO = {
    "北海道": (43.0642, 141.3469), "青森県": (40.8244, 140.7400), "岩手県": (39.7036, 141.1527),
    "宮城県": (38.2688, 140.8721), "秋田県": (39.7186, 140.1024), "山形県": (38.2404, 140.3633),
    "福島県": (37.7503, 140.4676), "茨城県": (36.3418, 140.4468), "栃木県": (36.5658, 139.8836),
    "群馬県": (36.3911, 139.0608), "埼玉県": (35.8617, 139.6455), "千葉県": (35.6046, 140.1233),
    "東京都": (35.6896, 139.6922), "神奈川県": (35.4478, 139.6425), "新潟県": (37.9026, 139.0236),
    "富山県": (36.6953, 137.2113), "石川県": (36.5947, 136.6256), "福井県": (36.0652, 136.2216),
    "山梨県": (35.6642, 138.5684), "長野県": (36.6513, 138.1810), "岐阜県": (35.3912, 136.7223),
    "静岡県": (34.9769, 138.3831), "愛知県": (35.1802, 136.9066), "三重県": (34.7303, 136.5086),
    "滋賀県": (35.0045, 135.8686), "京都府": (35.0116, 135.7681), "大阪府": (34.6937, 135.5023),
    "兵庫県": (34.6913, 135.1830), "奈良県": (34.6851, 135.8050), "和歌山県": (34.2261, 135.1675),
    "鳥取県": (35.5039, 134.2378), "島根県": (35.4723, 133.0505), "岡山県": (34.6618, 133.9350),
    "広島県": (34.3853, 132.4553), "山口県": (34.1861, 131.4705), "徳島県": (34.0658, 134.5593),
    "香川県": (34.3401, 134.0434), "愛媛県": (33.8416, 132.7657), "高知県": (33.5597, 133.5311),
    "福岡県": (33.6064, 130.4181), "佐賀県": (33.2494, 130.2988), "長崎県": (32.7448, 129.8737),
    "熊本県": (32.7898, 130.7417), "大分県": (33.2382, 131.6126), "宮崎県": (31.9111, 131.4239),
    "鹿児島県": (31.5602, 130.5581), "沖縄県": (26.2124, 127.6809),
}

# 地域未選択のときの基準地（普段どおり＝東京から検索。毎回同じ条件で測るため固定）
HOME_PREF = "東京都"


class BlockedError(Exception):
    pass


_kks = None
_reading_cache = {}


def reading_of(text):
    # 漢字キーワードのよみがな（検索用）。変換に失敗したら原文のまま
    global _kks
    if text in _reading_cache:
        return _reading_cache[text]
    try:
        if _kks is None:
            from pykakasi import kakasi
            _kks = kakasi()
        result = "".join(item["hira"] for item in _kks.convert(text))
    except Exception:
        result = text
    _reading_cache[text] = result
    return result


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
    if not KEYWORDS_FILE.exists():
        return rows
    with open(KEYWORDS_FILE, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 2 or row[0].strip() in ("", "keyword") or row[0].startswith("#"):
                continue
            location = row[2].strip() if len(row) > 2 else ""
            memo = row[3].strip() if len(row) > 3 else ""
            rows.append({"keyword": row[0].strip(), "url": row[1].strip(),
                         "location": location if location in PREF_GEO else "",
                         "memo": memo})
    return rows


def save_keywords(rows):
    with open(KEYWORDS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["keyword", "target_url", "location", "memo"])
        for r in rows:
            writer.writerow([r["keyword"], r["url"], r.get("location", ""),
                             r.get("memo", "")])


def load_competitors():
    rows = []
    if not COMPETITORS_FILE.exists():
        return rows
    with open(COMPETITORS_FILE, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 3 or row[0].strip() in ("", "name"):
                continue
            try:
                slot = int(row[2])
            except ValueError:
                continue
            rows.append({"name": row[0].strip(), "domain": row[1].strip(), "slot": slot})
    return rows


def save_competitors(rows):
    with open(COMPETITORS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "domain", "slot"])
        for r in rows:
            writer.writerow([r["name"], r["domain"], r["slot"]])


def competitor_matches(url_n, comp_n):
    # ドメイン指定はサイト単位で、パス付き指定はそのページ配下でマッチ
    if "/" in comp_n:
        return url_n == comp_n or url_n.startswith(comp_n + "/")
    host = url_n.split("/")[0]
    return host == comp_n or host.endswith("." + comp_n)


def ensure_location_column(path):
    # 旧フォーマットのCSVにlocation列を後付けする（1回だけ実行される）
    if not path.exists():
        return
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows or "location" in rows[0]:
        return
    rows[0].append("location")
    width = len(rows[0])
    out = [row + [""] * (width - len(row)) for row in rows]
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(out)


def read_csv_rows(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_csv(path, header, rows):
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(header)
        writer.writerows(rows)


def _check_blocked(page):
    if "/sorry/" in page.url or page.locator("form#captcha-form").count() > 0:
        raise BlockedError()


def _location_settled(page, pref):
    # SERPフッターの現在地表示が目的の都道府県になったか
    try:
        if page.locator("#footcnt").count() == 0:
            return False
        return pref in page.inner_text("#footcnt")
    except Exception:
        return False


def fetch_top9(page, keyword, location=""):
    # 位置はブラウザのGPS偽装で渡す（呼び出し側でset_geolocation済み）。
    # ここではGoogleがその位置を反映するまで待ってから結果を取る
    pref = location if location in PREF_GEO else HOME_PREF
    url = f"https://www.google.com/search?q={quote_plus(keyword)}&num=10&hl=ja&gl=jp"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    for sel in ("#L2AGLb", "button:has-text('すべて同意')", "button:has-text('同意する')"):
        try:
            if page.locator(sel).count() > 0:
                page.locator(sel).first.click(timeout=3000)
                page.wait_for_timeout(1000)
                break
        except Exception:
            pass
    _check_blocked(page)
    page.wait_for_selector("#search", timeout=15000)

    settled = False
    for attempt in range(16):
        if _location_settled(page, pref):
            settled = True
            break
        if attempt == 5:
            # 自動反映されないときはフッターの「現在地を更新」を押して促す
            try:
                page.locator('div[role="button"]:has-text("現在地を更新")') \
                    .first.click(timeout=1500)
            except Exception:
                pass
        if attempt == 10:
            page.reload(wait_until="domcontentloaded")
            _check_blocked(page)
            page.wait_for_selector("#search", timeout=15000)
        page.wait_for_timeout(700)

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
    return results[:MAX_RANK], settled


def build_state(running=False):
    keywords = load_keywords()
    competitors = load_competitors()  # 並びはファイル順（UIのindexと一致させる）
    results_rows = read_csv_rows(RESULTS_FILE)
    serp_rows = read_csv_rows(SERP_LOG_FILE)

    # SERPログ: keyword -> date -> {rank: url}（同日複数回は後の実行で上書き）
    serp_by_kw = {}
    for row in serp_rows:
        try:
            rank = int(row["rank"])
        except (ValueError, KeyError):
            continue
        if rank > MAX_RANK:
            continue
        key = (row["keyword"], row.get("location") or "")
        serp_by_kw.setdefault(key, {}).setdefault(row["date"], {})[rank] = row["url"]

    # results.csv（SERPログに無い日付の補完用）: (keyword, location) -> date -> row
    res_by_kw = {}
    for row in results_rows:
        key = (row["keyword"], row.get("location") or "")
        res_by_kw.setdefault(key, {})[row["date"]] = row

    entries = []
    all_dates = set()
    for kw in keywords:
        target_n = normalize(kw["url"])
        kw_key = (kw["keyword"], kw["location"])
        points = {}
        serp_dates = serp_by_kw.get(kw_key, {})
        # SERPログから対象URLの順位を再計算（URLを変更しても過去分から算出できる）
        for d, items in serp_dates.items():
            rank = None
            for r in sorted(items):
                if normalize(items[r]) == target_n:
                    rank = r
                    break
            points[d] = rank
        for d, row in res_by_kw.get(kw_key, {}).items():
            if d in points:
                continue
            if normalize(row.get("target_url", "")) != target_n:
                continue
            try:
                rv = int(row.get("rank", ""))
            except ValueError:
                rv = None
            points[d] = rv if (rv is not None and rv <= MAX_RANK) else None

        series = [{"date": d, "rank": points[d]} for d in sorted(points)]
        all_dates.update(points.keys())

        # 競合ごとの順位推移（SERPログから復元。記録がある日だけ）
        rivals = []
        for comp in competitors:
            comp_n = normalize(comp["domain"])
            cseries = []
            for d in sorted(serp_dates):
                crank = None
                for r in sorted(serp_dates[d]):
                    if competitor_matches(normalize(serp_dates[d][r]), comp_n):
                        crank = r
                        break
                cseries.append({"date": d, "rank": crank})
            rivals.append({"name": comp["name"], "slot": comp["slot"], "series": cseries})

        def comp_of(url):
            url_n = normalize(url)
            for comp in competitors:
                if competitor_matches(url_n, normalize(comp["domain"])):
                    return comp
            return None

        latest_serp = None
        if serp_dates:
            latest_date = max(serp_dates)
            items = []
            for r in sorted(serp_dates[latest_date]):
                u = serp_dates[latest_date][r]
                comp = comp_of(u)
                items.append({"rank": r, "url": u,
                              "me": normalize(u) == target_n,
                              "comp": comp["name"] if comp else None,
                              "slot": comp["slot"] if comp else None})
            latest_serp = {"date": latest_date, "items": items}

        entries.append({
            "keyword": kw["keyword"],
            "url": kw["url"],
            "location": kw["location"],
            "memo": kw["memo"],
            "reading": reading_of(kw["keyword"]),
            "series": series,
            "rivals": rivals,
            "serp": latest_serp,
            "last_checked": series[-1]["date"] if series else None,
        })

    return {
        "entries": entries,
        "competitors": competitors,
        "last_checked": max(all_dates) if all_dates else None,
        "running": running,
        "max_rank": MAX_RANK,
        "max_per_run": MAX_PER_RUN,
        "max_competitors": MAX_COMPETITORS,
    }


class Api:
    def __init__(self):
        self.window = None
        self._running = False
        self._lock = threading.Lock()

    def _emit(self, payload):
        if self.window:
            try:
                self.window.evaluate_js(
                    f"window.appEvent({json.dumps(payload, ensure_ascii=False)})")
            except Exception:
                pass

    def get_state(self):
        return build_state(running=self._running)

    def open_url(self, url):
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            webbrowser.open(url)
            return {"ok": True}
        return {"ok": False}

    def add_keyword(self, keyword, url, location="", memo=""):
        keyword, url = keyword.strip(), url.strip()
        location = location.strip() if location in PREF_GEO else ""
        memo = str(memo).strip()[:500]
        if not keyword or not url:
            return {"ok": False, "error": "キーワードとURLの両方を入れてね"}
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        rows = load_keywords()
        for r in rows:
            if (r["keyword"] == keyword and normalize(r["url"]) == normalize(url)
                    and r["location"] == location):
                return {"ok": False, "error": "同じキーワード×URL×地域がもう登録されてるよ"}
        rows.append({"keyword": keyword, "url": url, "location": location, "memo": memo})
        save_keywords(rows)
        return {"ok": True, "state": self.get_state()}

    def update_keyword(self, index, keyword, url, location="", memo=""):
        keyword, url = keyword.strip(), url.strip()
        location = location.strip() if location in PREF_GEO else ""
        memo = str(memo).strip()[:500]
        if not keyword or not url:
            return {"ok": False, "error": "キーワードとURLの両方を入れてね"}
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        rows = load_keywords()
        if not (0 <= index < len(rows)):
            return {"ok": False, "error": "対象が見つからなかったよ"}
        rows[index] = {"keyword": keyword, "url": url, "location": location, "memo": memo}
        save_keywords(rows)
        return {"ok": True, "state": self.get_state()}

    def delete_keyword(self, index):
        rows = load_keywords()
        if not (0 <= index < len(rows)):
            return {"ok": False, "error": "対象が見つからなかったよ"}
        rows.pop(index)
        save_keywords(rows)
        return {"ok": True, "state": self.get_state()}

    def reorder_keywords(self, order):
        rows = load_keywords()
        if not isinstance(order, list) or sorted(order) != list(range(len(rows))):
            return {"ok": False, "error": "並び順のデータが不正だよ"}
        save_keywords([rows[i] for i in order])
        return {"ok": True, "state": self.get_state()}

    def add_competitor(self, name, domain):
        name, domain = str(name).strip(), normalize(str(domain))
        if not domain or "." not in domain.split("/")[0]:
            return {"ok": False, "error": "ドメイン（例: atomfirm.com）を入れてね"}
        if not name:
            name = domain
        rows = load_competitors()
        if len(rows) >= MAX_COMPETITORS:
            return {"ok": False,
                    "error": f"競合は{MAX_COMPETITORS}つまで（グラフが見やすい上限）。"
                             "どれか削除してから追加してね"}
        if any(normalize(r["domain"]) == domain for r in rows):
            return {"ok": False, "error": "このドメインはもう登録されてるよ"}
        used = {r["slot"] for r in rows}
        slot = min(s for s in range(MAX_COMPETITORS) if s not in used)
        rows.append({"name": name[:30], "domain": domain, "slot": slot})
        save_competitors(rows)
        return {"ok": True, "state": self.get_state()}

    def update_competitor(self, index, name, domain):
        name, domain = str(name).strip(), normalize(str(domain))
        if not domain or "." not in domain.split("/")[0]:
            return {"ok": False, "error": "ドメイン（例: atomfirm.com）を入れてね"}
        if not name:
            name = domain
        rows = load_competitors()
        if not (0 <= index < len(rows)):
            return {"ok": False, "error": "対象が見つからなかったよ"}
        for i, r in enumerate(rows):
            if i != index and normalize(r["domain"]) == domain:
                return {"ok": False, "error": "このドメインはもう登録されてるよ"}
        rows[index] = {"name": name[:30], "domain": domain, "slot": rows[index]["slot"]}
        save_competitors(rows)
        return {"ok": True, "state": self.get_state()}

    def delete_competitor(self, index):
        rows = load_competitors()
        if not (0 <= index < len(rows)):
            return {"ok": False, "error": "対象が見つからなかったよ"}
        rows.pop(index)
        save_competitors(rows)
        return {"ok": True, "state": self.get_state()}

    def run_check(self, indices=None):
        with self._lock:
            if self._running:
                return {"ok": False, "error": "いまチェック実行中だよ"}
            self._running = True
        keywords = load_keywords()
        if not keywords:
            self._running = False
            return {"ok": False, "error": "キーワードがまだ登録されてないよ"}

        if indices is not None:
            candidates = [i for i in indices if 0 <= i < len(keywords)]
        else:
            candidates = list(range(len(keywords)))

        # 何件指定でも1回は最大5件・チェックが古い順（ブロック回避）
        skipped = max(0, len(candidates) - MAX_PER_RUN)
        if len(candidates) > MAX_PER_RUN:
            st = build_state()
            candidates = sorted(candidates,
                                key=lambda i: st["entries"][i]["last_checked"] or "")
            candidates = candidates[:MAX_PER_RUN]
        targets = [(i, keywords[i]) for i in candidates]

        if not targets:
            self._running = False
            return {"ok": False, "error": "チェック対象が見つからなかったよ"}

        threading.Thread(target=self._run, args=(targets, skipped), daemon=True).start()
        return {"ok": True, "count": len(targets), "skipped": skipped}

    def _run(self, targets, skipped):
        from playwright.sync_api import sync_playwright
        today = date.today().isoformat()
        done = 0
        blocked = False
        try:
            self._emit({"type": "run_start", "total": len(targets), "skipped": skipped})
            with sync_playwright() as p:
                # Chromeが無いPC（Windows等）ではEdgeで代用する
                context = None
                last_err = None
                for channel in ("chrome", "msedge"):
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir=str(PROFILE_DIR),
                            channel=channel,
                            headless=False,
                            locale="ja-JP",
                            user_agent=USER_AGENT,
                            viewport={"width": 1100, "height": 780},
                            args=["--disable-blink-features=AutomationControlled",
                                  "--window-size=1100,780"],
                        )
                        break
                    except Exception as e:
                        last_err = e
                if context is None:
                    raise last_err
                context.add_init_script(STEALTH_JS)
                context.grant_permissions(["geolocation"],
                                          origin="https://www.google.com")
                page = context.pages[0] if context.pages else context.new_page()

                # 地域なし→地域ありの順に実行（位置切り替えを最小限に）
                targets = sorted(targets, key=lambda t: bool(t[1]["location"]))

                for n, (idx, kw) in enumerate(targets):
                    if n > 0:
                        wait = round(random.uniform(15, 30))
                        self._emit({"type": "wait", "seconds": wait})
                        time.sleep(wait)
                    self._emit({"type": "kw_start", "n": n + 1, "total": len(targets),
                                "keyword": kw["keyword"], "location": kw["location"]})
                    lat, lng = PREF_GEO[kw["location"] or HOME_PREF]
                    context.set_geolocation({"latitude": lat, "longitude": lng})
                    try:
                        top9, settled = fetch_top9(page, kw["keyword"], kw["location"])
                    except BlockedError:
                        blocked = True
                        self._emit({"type": "blocked"})
                        break
                    except Exception as e:
                        self._emit({"type": "kw_error", "keyword": kw["keyword"],
                                    "message": str(e)[:120]})
                        continue

                    if not settled:
                        self._emit({"type": "kw_warn", "keyword": kw["keyword"],
                                    "message": f"{kw['location'] or HOME_PREF}の位置反映が"
                                               "確認できなかったよ（結果は記録した）"})

                    rank = "圏外"
                    target_n = normalize(kw["url"])
                    for r, u in enumerate(top9, 1):
                        if normalize(u) == target_n:
                            rank = r
                            break

                    append_csv(RESULTS_FILE,
                               ["date", "keyword", "target_url", "rank", "location"],
                               [[today, kw["keyword"], kw["url"], rank, kw["location"]]])
                    append_csv(SERP_LOG_FILE,
                               ["date", "keyword", "rank", "url", "location"],
                               [[today, kw["keyword"], r, u, kw["location"]]
                                for r, u in enumerate(top9, 1)])
                    done += 1
                    self._emit({"type": "kw_done", "keyword": kw["keyword"],
                                "rank": rank, "location": kw["location"]})

                context.close()
        except Exception as e:
            self._emit({"type": "fatal", "message": str(e)[:200]})
        finally:
            self._running = False
            self._emit({"type": "run_done", "done": done,
                        "blocked": blocked, "state": build_state()})


def main():
    for path in (KEYWORDS_FILE, RESULTS_FILE, SERP_LOG_FILE):
        ensure_location_column(path)
    api = Api()
    window = webview.create_window(
        "順位チェッカー",
        str(UI_FILE),
        js_api=api,
        width=1180,
        height=820,
        min_size=(760, 560),
    )
    api.window = window
    webview.start()


def selftest():
    # CIビルド後の動作確認用（画面は開かずに主要機能を通す）
    assert UI_FILE.exists(), "ui.html が見つからない"
    assert (BASE_DIR / "assets" / "clawd-coral2.gif").exists(), "assets が見つからない"
    assert reading_of("順位確認") == "じゅんいかくにん"
    state = build_state()
    assert "entries" in state and "competitors" in state
    from playwright.sync_api import sync_playwright  # import可能かだけ確認
    print("SELFTEST OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
