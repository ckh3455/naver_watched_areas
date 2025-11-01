#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 네이버 부동산 관심지역 크롤러 (GitHub Actions용)
# 2025-11-01 기준 안정화 버전: 진입 전략 강화 + 원본 뷰포트 복원

import asyncio, json, time, re, os
from datetime import datetime
from playwright.async_api import async_playwright
import gspread
from google.oauth2 import service_account


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


def setup_google_sheets():
    try:
        credentials_file = "service_account.json"
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        gc = gspread.authorize(credentials)
        spreadsheet_id = os.environ.get("SPREADSHEET_ID")
        spreadsheet = gc.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet("네이버 관심지역")
        print("✅ 구글 시트 연결 성공 (네이버 관심지역)")
        return worksheet
    except Exception as e:
        print(f"❌ 구글 시트 설정 실패: {e}")
        return None


class AggressiveCardScroll:
    def __init__(self, complex_id, complex_name):
        self.complex_id = complex_id
        self.complex_name = complex_name
        self.property_cards = []
        self.api_responses = []
        self.seen_article_nos = set()

    async def _enter_complex_page(self, page):
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

        try:
            await page.goto("https://new.land.naver.com/", wait_until='domcontentloaded')
            await page.wait_for_load_state('networkidle', timeout=20000)
        except:
            pass

        for u in urls:
            for wait in ("domcontentloaded", "load"):
                try:
                    resp = await page.goto(u, wait_until=wait, timeout=60000)
                    title = await page.title()
                    print(f"  [enter] url='{page.url}' wait={wait} status={getattr(resp,'status',None)} title='{title}'")
                    if "/404" in page.url or ("naver.com" in page.url and "/complexes/" not in page.url):
                        continue
                    if await ok():
                        return True
                except Exception as e:
                    print(f"  [enter] try fail: {e}")
                    continue
        return False

    async def _aggressive_warm_scroll(self, page):
        print("  ↪ 매물 탭 실패: 적극 스크롤 워밍업 시작")
        try:
            for _ in range(8):
                await page.keyboard.press("PageDown")
                await asyncio.sleep(0.4)
            for _ in range(5):
                await page.mouse.wheel(0, 1000)
                await asyncio.sleep(0.3)
        except:
            pass
        print("  ↪ 적극 스크롤 워밍업 종료")

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1920,1040"
                ]
            )
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1040},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0"
                ),
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                extra_http_headers={
                    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Referer": "https://new.land.naver.com/"
                }
            )

            await context.add_init_script("""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR','ko']});
Object.defineProperty(navigator, 'platform',  {get: () => 'Win32'});
            """)

            page = await context.new_page()

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

            entered = await self._enter_complex_page(page)
            if not entered:
                print("  ❌ 첫 페이지 진입 실패 → 건너뜀")
                await browser.close()
                return {'complex_name': self.complex_name, 'property_count': 0, 'properties': []}

            await asyncio.sleep(2)

            tab_clicked = False
            try:
                trade_btn = page.locator('a.complex_link span:has-text("매물")')
                await trade_btn.click(timeout=10000)
                await asyncio.sleep(2)
                tab_clicked = True
                print("  ✓ 매물 탭 클릭 완료")
            except Exception as e:
                print(f"  ⚠️  매물 탭 클릭 실패: {e}")
                await self._aggressive_warm_scroll(page)

            max_scrolls, no_new, prev_len = 100, 0, 0
            for i in range(max_scrolls):
                prev_len = len(self.property_cards)
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                if not tab_clicked and i < 6:
                    await page.keyboard.press("PageDown")
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
            return {'complex_name': self.complex_name, 'property_count': len(self.property_cards), 'properties': self.property_cards}


def format_property_data(p):
    r = p.get('raw_data', {})
    trade = r.get('tradeTypeName', '')
    price = r.get('dealOrWarrantPrc', '')
    return [p.get('complex_name', ''), trade, r.get('buildingName', ''), r.get('floorInfo', ''),
            r.get('areaName', ''), price, '', 1, r.get('realtorName', ''), r.get('articleConfirmYmd', ''),
            r.get('articleFeatureDesc', ''), r.get('cpName', ''), r.get('articleNo', '')]


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
    for idx, c in enumerate(COMPLEXES, 1):
        print(f"\n{'='*60}\n📍 [{idx}/{len(COMPLEXES)}] {c['name']} 크롤링 시작\n{'='*60}")
        t0 = time.time()
        try:
            r = await AggressiveCardScroll(c['id'], c['name']).run()
            dur = time.time() - t0
            print(f"✅ {c['name']} 완료: {r['property_count']}개 매물 ({dur:.1f}초)")
            for p in r['properties']:
                all_props.append(format_property_data(p))
        except Exception as e:
            print(f"❌ {c['name']} 실패: {e}")
        if idx < len(COMPLEXES):
            await asyncio.sleep(5)

    if all_props:
        ws.append_rows(all_props)
        print(f"✅ 구글 시트에 {len(all_props)}개 매물 기록 완료")
    else:
        print("⚠️  기록할 매물이 없습니다.")

    print("=" * 60)
    print(f"⏰ 종료시간: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
