#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 네이버 부동산 관심지역 크롤러 (GitHub Actions용)
# 안정화 포인트:
# - 첫 진입 전략(파라미터 단계적 시도)
# - 단지 클릭 보정(_focus_complex)로 상세 진입 확정
# - 매물 탭 셀렉터 다중 시도
# - headful(Xvfb) + 창/뷰포트 1920x1040 고정
# - 추출/포맷/시트 기록 로직은 원본 흐름 유지

import asyncio, json, time, re, os
from datetime import datetime
from playwright.async_api import async_playwright
import gspread
from google.oauth2 import service_account

# ====== 크롤링 대상 ======
COMPLEXES = [
    {"id": "3833", "name": "남산타운"},
    {"id": "110938", "name": "e편한세상옥수파크힐스"},
    {"id": "562", "name": "옥수극동"},
    {"id": "100692", "name": "래미안옥수리버젠"},
    {"id": "104917", "name": "마포래미안푸르지오"},
    {"id": "121608", "name": "마포프레스티지자이"},
    {"id": "119341", "name": "고덕아르테온"},
    {"id": "113907", "name": "고덕그라시움"},
    {"id": "113292", "name": "아크로리버하임"},
    {"id": "100514", "name": "광장힐스테이트"},
    {"id": "13457", "name": "더샵스타시티(주상복합)"},
    {"id": "75", "name": "광장현대5단지"},
    {"id": "101273", "name": "자연앤힐스테이트"},
    {"id": "101301", "name": "광교호수마을호반써밋"},
    {"id": "111038", "name": "광교중흥에스클래스(주상복합)"},
    {"id": "27508", "name": "판교푸르지오그랑블"},
    {"id": "3621", "name": "파크뷰(주상복합)"},
    {"id": "107014", "name": "일산요진와이시티(주거복합)"},
    {"id": "147880", "name": "안양역푸르지오더샵"},
    {"id": "175697", "name": "아크로베스티뉴"},
    {"id": "147229", "name": "힐스테이트구리역"},
    {"id": "119652", "name": "동탄역롯데캐슬(주상복합)"},
]

# ====== Google Sheets ======
def setup_google_sheets():
    try:
        credentials_file = "service_account.json"
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        gc = gspread.authorize(credentials)
        spreadsheet_id = os.environ.get("SPREADSHEET_ID")
        spreadsheet = gc.open_by_key(spreadsheet_id)
        try:
            ws = spreadsheet.worksheet("네이버 관심지역")
            print("✅ 구글 시트 연결 성공 (네이버 관심지역)")
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet("네이버 관심지역", rows=1000, cols=20)
            print("✅ 구글 시트 탭 생성 완료 (네이버 관심지역)")
        return ws
    except Exception as e:
        print(f"❌ 구글 시트 설정 실패: {e}")
        return None

# ====== 크롤러 ======
class AggressiveCardScroll:
    def __init__(self, complex_id, complex_name):
        self.complex_id = complex_id
        self.complex_name = complex_name
        self.property_cards = []
        self.seen_article_nos = set()

    async def _enter_complex_page(self, page):
        """첫 진입: 파라미터 없는 URL → 최소 파라미터 → 기존 URL, 각 wait 2패턴"""
        id_ = self.complex_id
        urls = [
            f"https://new.land.naver.com/complexes/{id_}",
            f"https://new.land.naver.com/complexes/{id_}?a=APT&b=A1",
            f"https://new.land.naver.com/complexes/{id_}?ms=37.4779802,127.0413966,16&a=APT&b=A1&e=RETAIL"
        ]

        async def ok():
            for sel in [
                'a.complex_link span:has-text("매물")',
                "h2, h3",
                "div[class*=complex], header[class*=complex]"
            ]:
                try:
                    if await page.locator(sel).first.is_visible():
                        return True
                except:
                    continue
            return False

        # 홈 워밍업
        try:
            await page.goto("https://new.land.naver.com/", wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_load_state('networkidle', timeout=20000)
        except:
            pass

        for u in urls:
            for wait in ("domcontentloaded", "load"):
                try:
                    resp = await page.goto(u, wait_until=wait, timeout=60000)
                    title = ""
                    try: title = await page.title()
                    except: pass
                    print(f"  [enter] url='{page.url}' wait={wait} status={getattr(resp,'status',None)} title='{title}'")
                    if "/404" in page.url or ("naver.com" in page.url and "/complexes/" not in page.url):
                        continue
                    if await ok():
                        return True
                except Exception as e:
                    print(f"  [enter] try fail: {e}")
                    continue
        return False

    async def _focus_complex(self, page):
        """
        상세로 확정 진입 보정:
        - /complexes/<id> 링크 직접 클릭
        - 검색창에 단지명 입력 후 제안 클릭
        - 리스트 패널/버튼에서 단지명 클릭
        """
        target_id = self.complex_id
        target_name = self.complex_name
        if f"/complexes/{target_id}" in page.url:
            return True

        # 1) 직접 링크
        try:
            link = page.locator(f'a[href*="/complexes/{target_id}"]').first
            if await link.count() > 0:
                await link.scroll_into_view_if_needed()
                await link.click(timeout=3000)
                await page.wait_for_url(lambda url: f"/complexes/{target_id}" in url, timeout=5000)
                return True
        except: pass

        # 2) 검색창 → 입력 → 제안 클릭
        try:
            search_input = page.locator('input[id*="search"], input[placeholder*="단지"], input[type="search"]').first
            if await search_input.count() > 0:
                await search_input.click(timeout=2000)
                await search_input.fill(target_name)
                try:
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(500)
                except: pass
                suggest = page.locator(f'li:has-text("{target_name}")').first
                if await suggest.count() > 0:
                    await suggest.click(timeout=3000)
                    await page.wait_for_url(lambda url: f"/complexes/{target_id}" in url, timeout=5000)
                    return True
        except: pass

        # 3) 리스트/패널에서 단지명 클릭
        try:
            for cont in ['#listContents', 'aside', 'section', 'div[class*="list"]', 'div[role="list"]']:
                item = page.locator(f'{cont} :is(a,button,div):has-text("{target_name}")').first
                if await item.count() > 0:
                    await item.scroll_into_view_if_needed()
                    await item.click(timeout=3000)
                    await page.wait_for_url(lambda url: f"/complexes/{target_id}" in url, timeout=5000)
                    return True
        except: pass

        # 4) 마지막: id가 들어간 어떤 링크/버튼이라도
        try:
            any_id = page.locator(':is(a,button)[href*="/complexes/"], :is(a,button)[onclick]')
            n = await any_id.count()
            for i in range(min(n, 30)):
                el = any_id.nth(i)
                href = None
                try: href = await el.get_attribute("href")
                except: pass
                text_ok = ""
                try:
                    if await el.is_visible():
                        text_ok = (await el.inner_text()).strip()
                except: pass
                if (href and f"/complexes/{target_id}" in href) or (target_name in text_ok):
                    await el.scroll_into_view_if_needed()
                    await el.click(timeout=3000)
                    await page.wait_for_url(lambda url: f"/complexes/{target_id}" in url, timeout=5000)
                    return True
        except: pass

        return False

    async def _aggressive_warm_scroll(self, page):
        print("  ↪ 매물 탭 실패: 적극 스크롤 워밍업 시작")
        try:
            for _ in range(8):
                await page.keyboard.press("PageDown"); await asyncio.sleep(0.4)
            for _ in range(5):
                await page.mouse.wheel(0, 1000); await asyncio.sleep(0.3)
        except: pass
        print("  ↪ 적극 스크롤 워밍업 종료")

    async def run(self):
        async with async_playwright() as p:
            # 프록시(선택): secrets.PROXY_SERVER가 있으면 자동 적용
            proxy_env = os.environ.get("PROXY_SERVER", "").strip()
            launch_kwargs = {
                "headless": False,
                "args": ["--disable-blink-features=AutomationControlled", "--window-size=1920,1040"]
            }
            if proxy_env:
                launch_kwargs["proxy"] = {"server": proxy_env}

            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1040},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0"),
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                extra_http_headers={
                    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Referer": "https://land.naver.com/"
                }
            )
            # 쿠키 문자열(선택): secrets.COOKIE_STRING 제공 시 사전 주입
            cookie_str = os.environ.get("COOKIE_STRING", "").strip()
            if cookie_str:
                jar = []
                for part in [c.strip() for c in cookie_str.split(";") if "=" in c]:
                    k, v = part.split("=", 1)
                    for domain in [".naver.com", ".land.naver.com", ".new.land.naver.com"]:
                        jar.append({"name": k.strip(), "value": v.strip(), "domain": domain, "path": "/", "httpOnly": False, "secure": True})
                await context.add_cookies(jar)

            await context.add_init_script("""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR','ko']});
Object.defineProperty(navigator, 'platform',  {get: () => 'Win32'});
            """)

            page = await context.new_page()

            # API 응답 훅: complexArticleList에서 카드 수집
            async def handle_response(response):
                if 'complexArticleList' in response.url:
                    try:
                        js = await response.json()
                        if 'result' in js:
                            for item in js['result'].get('list', []):
                                aid = str(item.get('articleNo', ''))
                                if aid and aid not in self.seen_article_nos:
                                    self.seen_article_nos.add(aid)
                                    self.property_cards.append({
                                        'raw_data': item,
                                        'complex_name': self.complex_name
                                    })
                    except:
                        pass
            page.on('response', handle_response)

            # 1) 첫 진입
            entered = await self._enter_complex_page(page)
            if not entered:
                print("  ❌ 첫 페이지 진입 실패 → 건너뜀")
                await browser.close()
                return {'complex_name': self.complex_name, 'property_count': 0, 'properties': []}

            # 2) 상세 진입 보정 (단지 클릭)
            if f"/complexes/{self.complex_id}" not in page.url:
                clicked = await self._focus_complex(page)
                print(f"  [enter-fix] click complex → {'OK' if clicked else 'FAIL'} (url={page.url})")
                if not clicked:
                    await browser.close()
                    return {'complex_name': self.complex_name, 'property_count': 0, 'properties': []}

            await asyncio.sleep(1.5)

            # 3) 매물 탭 클릭 (다중 셀렉터)
            tab_clicked = False
            tab_selectors = [
                'a.complex_link span:has-text("매물")',
                'a:has-text("매물")',
                'button:has-text("매물")',
                '[role="tab"]:has-text("매물")'
            ]
            for sel in tab_selectors:
                try:
                    btn = page.locator(sel)
                    await btn.click(timeout=2000)
                    await asyncio.sleep(1.0)
                    print(f"  ✓ 매물 탭 클릭 완료 ({sel})")
                    tab_clicked = True
                    break
                except Exception:
                    continue

            if not tab_clicked:
                print("  ⚠️  매물 탭 클릭 실패: 백업 워밍업 스크롤로 진행")
                await self._aggressive_warm_scroll(page)

            # 4) 스크롤 수집(원본 루틴)
            max_scrolls, no_new, prev_len = 100, 0, 0
            for i in range(max_scrolls):
                prev_len = len(self.property_cards)
                try:
                    await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                except: pass
                if not tab_clicked and i < 6:
                    try:
                        await page.keyboard.press("PageDown")
                    except: pass
                await asyncio.sleep(1.5)
                cur_len = len(self.property_cards)
                if cur_len > prev_len:
                    no_new = 0
                    print(f"  📊 스크롤 {i+1}: {cur_len}개 매물")
                else:
                    no_new += 1
                if no_new >= 3:
                    print(f"  ✓ 스크롤 완료 (연속 {no_new}회 변화 없음)")
                    break

            await browser.close()
            return {
                'complex_name': self.complex_name,
                'property_count': len(self.property_cards),
                'properties': self.property_cards
            }

# ====== 포맷 ======
def format_property_data(p):
    r = p.get('raw_data', {})
    trade = r.get('tradeTypeName', '')
    price = r.get('dealOrWarrantPrc', '')
    # 월세 표기
    if trade == '월세':
        dep = r.get('dealOrWarrantPrc', '')
        mon = r.get('rentPrc', '')
        if dep and mon: price = f"{dep}/{mon}만원"
        elif mon: price = f"{mon}만원"
    area = r.get('areaName', '') or (str(r.get('area1', '')) + ("m²" if r.get('area1') else ""))
    return [
        p.get('complex_name', ''),                    # 단지명
        trade,                                        # 거래구분
        r.get('buildingName', ''),                    # 동
        r.get('floorInfo', ''),                       # 층수
        area,                                         # 면적
        price,                                        # 가격
        '',                                           # 가격변동(비움)
        1,                                            # 중복업소(기본 1)
        r.get('realtorName', '') or 'Unknown',       # 중개업소
        r.get('articleConfirmYmd', '') or 'Unknown', # 등록일자
        r.get('articleFeatureDesc', '') or '',       # 특기사항
        r.get('cpName', '') or 'Unknown',            # 제공
        r.get('articleNo', '')                       # 매물번호
    ]

# ====== 메인 ======
async def main():
    print("=" * 60)
    print("🏢 네이버 부동산 크롤러 시작")
    print(f"⏰ 시작시간: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 60)

    ws = setup_google_sheets()
    if not ws:
        print("❌ 시트 연결 실패"); return
    ws.clear()
    ws.append_row(["단지명","거래구분","동","층수","면적","가격","가격변동","중복업소","중개업소","등록일자","특기사항","제공","매물번호"])
    print("✅ 구글 시트 초기화 완료")

    all_props, results = [], []
    total_start = time.time()
    for idx, c in enumerate(COMPLEXES, 1):
        print(f"\n{'='*60}\n📍 [{idx}/{len(COMPLEXES)}] {c['name']} 크롤링 시작\n{'='*60}")
        t0 = time.time()
        try:
            r = await AggressiveCardScroll(c['id'], c['name']).run()
            dur = time.time() - t0
            print(f"✅ {c['name']} 완료: {r['property_count']}개 매물 ({dur:.1f}초)")
            for p in r['properties']:
                all_props.append(format_property_data(p))
            results.append({"complex_name": c["name"], "property_count": r["property_count"], "duration_seconds": dur, "status": "success"})
        except Exception as e:
            dur = time.time() - t0
            print(f"❌ {c['name']} 실패: {e} ({dur:.1f}초)")
            results.append({"complex_name": c["name"], "property_count": 0, "duration_seconds": dur, "status": "error", "error": str(e)})
        if idx < len(COMPLEXES):
            await asyncio.sleep(5)

    if all_props:
        ws.append_rows(all_props)
        print(f"✅ 구글 시트에 {len(all_props)}개 매물 기록 완료")
    else:
        print("⚠️  기록할 매물이 없습니다.")

    total_dur = time.time() - total_start
    succ = [r for r in results if r["status"] == "success"]
    fail = [r for r in results if r["status"] == "error"]
    print("=" * 60)
    print(f"⏰ 종료시간: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"⏱️  전체 소요시간: {total_dur:.1f}초 ({total_dur/60:.1f}분)")
    print(f"✅ 성공한 단지: {len(succ)}개 | ❌ 실패한 단지: {len(fail)}개")
    print("=" * 60)

    # 결과 JSON(아티팩트용)
    with open('crawling_results.json', 'w', encoding='utf-8') as f:
        json.dump({
            "total_duration_seconds": total_dur,
            "total_duration_minutes": total_dur/60,
            "start_time": datetime.fromtimestamp(total_start).strftime('%Y-%m-%d %H:%M:%S'),
            "end_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "successful_count": len(succ),
            "failed_count": len(fail),
            "results": results
        }, f, ensure_ascii=False, indent=2)
    print("💾 결과가 'crawling_results.json' 파일에 저장되었습니다.")

if __name__ == "__main__":
    asyncio.run(main())
