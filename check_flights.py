import os
import re
import json
import hashlib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notified_deals.json")

# =========================================================================
# 일본 / 동남아 항공 특가 필터 설정
# =========================================================================
AIRLINE_REQUIRED_WORDS = [
    "항공", "항공권", "비행기", "노선", "취항", "왕복", "편도",
    "진마켓", "찜특가", "메가얼리버드", "사이다특가",
    "진에어", "제주항공", "티웨이", "이스타", "에어서울", "에어부산",
    "에어프레미아", "대한항공", "아시아나", "피치항공", "스쿠트", "비엣젯", "에어아시아",
]

PROMO_MUST_WORDS = [
    "특가", "할인", "프로모션", "얼리버드", "진마켓", "찜", "사이다", "땡처리", "세일", "원~", "만원"
]

REGION_WORDS = [
    # 일본
    "일본", "도쿄", "나리타", "하네다", "오사카", "간사이", "후쿠오카", "삿포로", "치토세",
    "오키나와", "나하", "나고야", "다카마쓰", "마쓰야마", "구마모토", "가고시마", "미야코지마",
    "시즈오카", "히로시마", "사가", "기타큐슈", "센다이", "이시가키",
    # 동남아
    "동남아", "태국", "방콕", "치앙마이", "푸켓", "베트남", "다낭", "나트랑", "냐짱",
    "푸꾸옥", "하노이", "호치민", "달랏", "필리핀", "세부", "보라카이", "보홀", "마닐라", "클락",
    "코타키나발루", "말레이시아", "쿠알라룸푸르", "싱가포르", "발리", "인도네시아", "라오스",
    # LCC 전노선 대형 특가 (일본/동남아 포함)
    "진마켓", "찜특가", "메가얼리버드", "사이다특가", "전노선",
]

EXCLUDE_WORDS = [
    "주가", "실적", "채용", "파업", "결항", "회항", "사고", "화재", "국토부", "합병", "지분"
]

RSS_SOURCES = [
    ("뽐뿌 핫딜", "https://www.ppomppu.co.kr/rss.php?id=ppomppu", False),
    ("뽐뿌 해외핫딜", "https://www.ppomppu.co.kr/rss.php?id=ppomppu4", False),
    ("루리웹 핫딜", "https://bbs.ruliweb.com/market/board/1020/rss", False),
    (
        "Google 뉴스(항공특가)",
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote("항공권 특가 OR 진마켓 OR 찜특가 OR 메가얼리버드 OR 사이다특가")
        + "&hl=ko&gl=KR&ceid=KR:ko",
        True,
    ),
]


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_state(keys_list):
    # Keep latest 500 items so file never grows large
    trimmed = keys_list[-500:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def make_key(title, link):
    norm_title = re.sub(r"\s+", " ", title).strip().lower()
    return hashlib.sha256(f"{norm_title}|{link}".encode("utf-8")).hexdigest()[:16]


def is_recent(pub_date_str, hours=48):
    if not pub_date_str:
        return True
    try:
        dt = parsedate_to_datetime(pub_date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - dt) <= timedelta(hours=hours)
    except Exception:
        return True


def matches_japan_sea_flight_deal(title, is_news):
    t = title.lower()
    if any(ex in t for ex in EXCLUDE_WORDS):
        return False
    if not any(w.lower() in t for w in AIRLINE_REQUIRED_WORDS):
        return False
    if not any(r.lower() in t for r in REGION_WORDS):
        return False
    if is_news and not any(p.lower() in t for p in PROMO_MUST_WORDS):
        return False
    return True


def fetch_rss_items(source_name, url, is_news):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=12) as resp:
        xml_bytes = resp.read()

    root = ET.fromstring(xml_bytes)
    results = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not title or not link:
            continue
        if is_news and not is_recent(pub_date, hours=48):
            continue
        if matches_japan_sea_flight_deal(title, is_news):
            results.append({
                "source": source_name,
                "title": title,
                "link": link,
                "pub_date": pub_date,
            })
    return results


def send_discord(webhook_url, message):
    payload = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url.strip(),
        data=payload,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"Discord sent: HTTP {resp.status}")


def main():
    seen_list = load_state()
    seen_set = set(seen_list)
    new_deals = []

    for name, url, is_news in RSS_SOURCES:
        try:
            items = fetch_rss_items(name, url, is_news)
            for it in items:
                k = make_key(it["title"], it["link"])
                if k not in seen_set:
                    seen_set.add(k)
                    seen_list.append(k)
                    new_deals.append(it)
        except Exception as e:
            print(f"[{name}] 조회 오류: {e}")

    discord_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    kst_now = datetime.now(timezone.utc) + timedelta(hours=9)

    if new_deals:
        lines = []
        for d in new_deals[:8]:
            lines.append(f"• **[{d['source']}]** {d['title']}\n  {d['link']}")
        msg = "✈️ **일본 · 동남아 항공 특가 소식!**\n\n" + "\n\n".join(lines)
        if len(new_deals) > 8:
            msg += f"\n\n... 외 {len(new_deals) - 8}건 추가"
        print(msg)

        if discord_url:
            # Discord message max length is 2000 chars
            send_discord(discord_url, msg[:1950])
        save_state(seen_list)
    else:
        print(f"[{kst_now:%Y-%m-%d %H:%M:%S} KST] 새로운 일본·동남아 항공 특가 없음")
        if os.environ.get("SEND_TEST_ON_MANUAL") == "1" and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
            test_msg = (
                "🔔 [GitHub 테스트] **일본 · 동남아 항공 특가 알람 봇**이 정상 작동 중입니다!\n"
                "• 감시 대상: 뽐뿌/루리웹 항공 핫딜 + Google 뉴스 항공사 특가(일본·동남아·LCC 전노선)"
            )
            if discord_url:
                send_discord(discord_url, test_msg)


if __name__ == "__main__":
    main()
