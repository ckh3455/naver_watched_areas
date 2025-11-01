#!/usr/bin/env python3
"""
GitHub Actions용 네이버 부동산 크롤러
매일 자동으로 여러 단지의 매물 정보를 수집하여 Google Sheets의
'네이버 관심지역' 탭에 기록합니다.
(추출 흐름 유지. '매물' 탭 클릭 실패여도 적극 스크롤로 수집 시도)
"""

import asyncio
from playwright.async_api import async_playwright
import json
from datetime import datetime
import re
import os
import time
import gspread
from google.oauth2 import service_account

# 크롤링 대상 단지 목록
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
    """Google Sheets 연결"""
    try:
        credentials_file = "service_account.json"
        if not os.path.exists(credentials_file):
            print(f"❌ 서비스 계정 파일이 없습니다: {credentials_file}")
            return None

        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        gc = gspread.authorize(credentials)

        spreadsheet_id = os.environ.get(
            "SPREADSHEET_ID",
            "1FfeV5dkq7MTe443iMIYjztueWcUkv8ngsrDQmEzeTA4",
        )
        spreadsheet = gc.open_by_key(spreadsheet_id)

        try:
            worksheet = spreadsheet.worksheet("네이버 관심지역")
            print("✅ 구글 시트 연결 성공 (네이버 관심지역)")
            return worksheet
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title="네이버 관심지역", rows=1000, cols=20
            )
            print("✅ 구글 시트 탭 생성 완료 (네이버 관심지역)")
            return worksheet

    except Exception as e:
        print(f"❌ 구글 시트 설정 실패: {e}")
        return None


class AggressiveCardScroll:
    """네이버 부동산 매물 크롤러 (추출 흐름 유지)"""
    
    def __init__(self, complex_id, complex_name):
        self.complex_id = complex_id
        self.complex_name = complex_name
        self.property_cards = []
        self.api_responses = []
        self.seen_article_nos = set()

    async def _aggressive_warm_scroll(self, page):
        """매물탭 실패 시: 다양한 방법으로 강제 스크롤/포커스 → API 유도"""
        print("  ↪ 매물 탭 실패: 적극 스크롤 워밍업 시작")

        # 1) 키보드 스크롤(빠른 페이징 유도)
        try:
            for _ in range(3):
                await page.keyboard.press("End")
                await asyncio.sleep(0.6)
            for _ in range(3):
                await page.keyboard.press("PageDown")
                await asyncio.sleep(0.4)
        except Exception:
            pass

        # 2) 마우스 휠
        try:
            for _ in range(8):
                await page.mouse.wheel(0, 1200)
                await asyncio.sleep(0.25)
        except Exception:
            pass

        # 3) 리스트 컨테이너 후보에 직접 스크롤 (가능한 셀렉터 폭넓게 시도)
        candidates = [
            "#listContents",
            "div[class*=list]", "div[class*=sale]", "div[role='list']",
            "section[class*=list]", "div[class*=item_list]",
        ]
        for sel in candidates:
            try:
                count = await page.locator(sel).count()
                if count == 0:
                    continue
                # 여러 개면 앞쪽 몇 개만 시도
                for idx in range(min(count, 3)):
                    locator = page.locator(sel).nth(idx)
                    await locator.evaluate(
                        "(el) => { el.scrollTop = 0; }"
                    )
                    await asyncio.sleep(0.2)
                    for _ in range(6):
                        await locator.evaluate(
                            "(el) => { el.scrollTop = el.scrollTop + el.clientHeight * 0.9; }"
                        )
                        await asyncio.sleep(0.35)
                print(f"  ↪ 컨테이너 스크롤 시도: {sel}")
            except Exception:
                continue

        # 4) 뷰포트 전체 스크롤(원본과 동일)
        for _ in range(6):
            try:
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            except Exception:
                pass
            await asyncio.sleep(0.8)

        print("  ↪ 적극 스크롤 워밍업 종료")

    async def run(self):
        """크롤링 실행"""
        async with async_playwright() as p:
            # 원본과 유사한 접근 조건 (headful with Xvfb)
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                viewport={'width': 1366, 'height': 768},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0"
                ),
                locale='ko-KR',
                timezone_id='Asia/Seoul',
                extra_http_headers={
                    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
                    'Referer': 'https://new.land.naver.com/'
                }
            )
            await context.add_init_script("""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR','ko']});
Object.defineProperty(navigator, 'platform',  {get: () => 'Win32'});
            """)
            page = await context.new_page()
            
            # API 응답 캡처 (원본 그대로)
            async def handle_response(response):
                if 'complexPyeongDetailList' in response.url or 'complexArticleList' in response.url:
                    try:
                        json_data = await response.json()
                        self.api_responses.append(json_data)
                        if 'result' in json_data and json_data['result']:
                            list_data = json_data['result'].get('list', [])
                            for item in list_data:
                                article_no = str(item.get('articleNo', ''))
                                if article_no and article_no not in self.seen_article_nos:
                                    self.seen_article_nos.add(article_no)
                                    self.property_cards.append({
                                        'raw_data': item,
                                        'complex_name': self.complex_name,
                                        'extracted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                    })
                    except Exception as e:
                        print(f"⚠️  API 응답 파싱 오류: {e}")
            page.on('response', handle_response)
            
            # 홈 워밍업 → 단지 진입
            try:
                await page.goto("https://new.land.naver.com/", wait_until='domcontentloaded', timeout=60000)
                await page.wait_for_load_state('networkidle', timeout=30000)
                await asyncio.sleep(1.0)
            except Exception:
                pass

            url = f"https://new.land.naver.com/complexes/{self.complex_id}?ms=37.4779802,127.0413966,16&a=APT&b=A1&e=RETAIL"
            resp = await page.goto(url, wait_until='domcontentloaded', timeout=60000)

            # 디버그(접근 결과만 기록)
            try:
                ua = await page.evaluate("() => navigator.userAgent")
            except Exception:
                ua = "N/A"
            try:
                status = resp.status if resp else None
            except Exception:
                status = None
            try:
                title = await page.title()
            except Exception:
                title = ""
            print(f"  [debug] after goto: url='{page.url}' status={status} title='{title}'")
            print(f"  [debug] UA: {ua}")
            try:
                if ("new.land.naver.com" not in page.url) or ("/complexes/" not in page.url):
                    await page.screenshot(path=f"snap_{self.complex_id}_redirect.png", full_page=True)
                    print("  [debug] redirected (saved snap)")
            except Exception:
                pass

            await asyncio.sleep(2)
            
            # 매물 탭 클릭 (실패해도 원본처럼 '적극 스크롤'로 바로 수집 시도)
            tab_clicked = False
            try:
                trade_button = page.locator('a.complex_link span:has-text("매물")')
                await trade_button.click(timeout=10000)
                await asyncio.sleep(2)
                print("  ✓ 매물 탭 클릭 완료")
                tab_clicked = True
            except Exception as e:
                try:
                    await page.screenshot(path=f"snap_{self.complex_id}_tab_click_fail.png", full_page=True)
                except Exception:
                    pass
                print(f"  ⚠️  매물 탭 클릭 실패: {e}")
                # ▶ 원본과 동일 동작: 탭 실패해도 선행 워밍업 스크롤 후 본 스크롤 루프 진입
                await self._aggressive_warm_scroll(page)
            
            # (원본과 동일) 스크롤 및 데이터 수집
            max_scrolls = 100
            no_new_data_count = 0
            for scroll_num in range(max_scrolls):
                previous_count = len(self.property_cards)
                try:
                    await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                except Exception:
                    pass
                # 추가로 휠/키보드 섞기(탭 실패 케이스에서 관성 유지)
                if not tab_clicked and scroll_num < 6:
                    try:
                        await page.mouse.wheel(0, 1000)
                        await page.keyboard.press("PageDown")
                    except Exception:
                        pass

                await asyncio.sleep(1.5)

                current_count = len(self.property_cards)
                if current_count > previous_count:
                    no_new_data_count = 0
                    print(f"  📊 스크롤 {scroll_num + 1}: {current_count}개 매물")
                else:
                    no_new_data_count += 1
                if no_new_data_count >= 3:
                    print(f"  ✓ 스크롤 완료 (연속 {no_new_data_count}회 변화 없음)")
                    break
            
            await browser.close()
        
        return {
            'complex_name': self.complex_name,
            'property_count': len(self.property_cards),
            'properties': self.property_cards
        }


def format_property_data(property_data):
    """매물 데이터 포맷팅 (원본 그대로)"""
    raw_data = property_data.get('raw_data', {})
    area_name = raw_data.get('areaName', '')
    area1 = raw_data.get('area1', '')
    area2 = raw_data.get('area2', '')
    if not area_name:
        area = "Unknown"
    elif area1 and area2 and area1 != area2:
        area = f"{area1}/{area2}m²"
    elif area1:
        area = f"{area1}m²"
    else:
        area = f"{area_name}m²"
    special_notes = []
    direction = raw_data.get('direction', '')
    if direction:
        special_notes.append(f"방향: {direction}")
    feature_desc = raw_data.get('articleFeatureDesc', '')
    if feature_desc:
        if "제공" in feature_desc:
            feature_desc = feature_desc.split("제공")[0].strip()
        special_notes.append(feature_desc)
    tag_list = raw_data.get('tagList', [])
    if tag_list:
        tags = " | ".join(tag_list)
        special_notes.append(f"태그: {tags}")
    special_notes_str = " | ".join(special_notes) if special_notes else ""
    broker_name = raw_data.get('realtorName', '')
    if broker_name and broker_name != "Unknown":
        remove_strings = ['공인중개사사무소', '(주)', '중개법인', '주식회사', '부동산중개', 
                         '부동산중개법인주식회사', '부동산중개법인', '공인중개사', '부동산']
        for remove_str in remove_strings:
            broker_name = broker_name.replace(remove_str, '')
        broker_name = re.sub(r'\d+', '', broker_name).strip()
    else:
        broker_name = "Unknown"
    date_str = raw_data.get('articleConfirmYmd', '')
    if date_str and len(date_str) == 8 and date_str.isdigit():
        registration_date = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}"
    else:
        registration_date = date_str or "Unknown"
    trade_type = raw_data.get('tradeTypeName', '')
    price = raw_data.get('dealOrWarrantPrc', '')
    if trade_type == '월세':
        deposit = raw_data.get('dealOrWarrantPrc', '')
        monthly = raw_data.get('rentPrc', '')
        if deposit and monthly:
            price = f"{deposit}/{monthly}만원"
        elif deposit:
            price = deposit
        elif monthly:
            price = f"{monthly}만원"
    return [
        property_data.get('complex_name', ''),
        trade_type,
        raw_data.get('buildingName', ''),
        raw_data.get('floorInfo', ''),
        area,
        price,
        '',
        1,
        broker_name,
        registration_date,
        special_notes_str,
        raw_data.get('cpName', '') or 'Unknown',
        raw_data.get('articleNo', '')
    ]


async def main():
    print("=" * 60)
    print("🏢 네이버 부동산 크롤러 시작")
    print(f"⏰ 시작시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    worksheet = setup_google_sheets()
    if not worksheet:
        print("❌ 구글 시트 연결 실패. 종료합니다.")
        return
    worksheet.clear()
    headers = ["단지명", "거래구분", "동", "층수", "면적", "가격", "가격변동", 
               "중복업소", "중개업소", "등록일자", "특기사항", "제공", "매물번호"]
    worksheet.append_row(headers)
    print("✅ 구글 시트 초기화 완료")
    results = []
    all_properties = []
    total_start_time = time.time()
    for idx, complex_info in enumerate(COMPLEXES, 1):
        print(f"\n{'='*60}")
        print(f"📍 [{idx}/23] {complex_info['name']} 크롤링 시작")
        print(f"{'='*60}")
        complex_start_time = time.time()
        try:
            crawler = AggressiveCardScroll(complex_info['id'], complex_info['name'])
            result = await crawler.run()
            complex_end_time = time.time()
            complex_duration = complex_end_time - complex_start_time
            property_count = result['property_count']
            print(f"✅ {complex_info['name']} 완료: {property_count}개 매물 ({complex_duration:.1f}초)")
            if result.get('properties'):
                for property_data in result['properties']:
                    formatted_row = format_property_data(property_data)
                    all_properties.append(formatted_row)
            results.append({
                'complex_name': complex_info['name'],
                'property_count': property_count,
                'duration_seconds': complex_duration,
                'status': 'success'
            })
        except Exception as e:
            complex_end_time = time.time()
            complex_duration = complex_end_time - complex_start_time
            print(f"❌ {complex_info['name']} 실패: {e} ({complex_duration:.1f}초)")
            results.append({
                'complex_name': complex_info['name'],
                'property_count': 0,
                'duration_seconds': complex_duration,
                'status': 'error',
                'error': str(e)
            })
        if idx < len(COMPLEXES):
            print("⏳ 5초 대기...")
            await asyncio.sleep(5)
    if all_properties:
        print(f"\n📝 구글 시트에 {len(all_properties)}개 매물 기록 중...")
        worksheet.append_rows(all_properties)
        print("✅ 구글 시트 기록 완료")
    else:
        print("⚠️  기록할 매물 데이터가 없습니다")
    total_end_time = time.time()
    total_duration = total_end_time - total_start_time
    successful = [r for r in results if r['status'] == 'success']
    failed = [r for r in results if r['status'] == 'error']
    total_properties = sum(r['property_count'] for r in results)
    print(f"\n{'='*60}")
    print("📊 전체 결과 요약")
    print(f"{'='*60}")
    print(f"⏰ 종료시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⏱️  전체 소요시간: {total_duration:.1f}초 ({total_duration/60:.1f}분)")
    print(f"✅ 성공한 단지: {len(successful)}개")
    print(f"❌ 실패한 단지: {len(failed)}개")
    print(f"🏠 총 매물 수: {total_properties}개")
    print(f"\n📋 단지별 상세 결과:")
    for i, result in enumerate(results, 1):
        status_icon = "✅" if result['status'] == 'success' else "❌"
        print(f"{i:2d}. {status_icon} {result['complex_name']:20s} | {result['property_count']:4d}개 | {result['duration_seconds']:5.1f}초")
    result_data = {
        'total_duration_seconds': total_duration,
        'total_duration_minutes': total_duration/60,
        'start_time': datetime.fromtimestamp(total_start_time).strftime('%Y-%m-%d %H:%M:%S'),
        'end_time': datetime.fromtimestamp(total_end_time).strftime('%Y-%m-%d %H:%M:%S'),
        'successful_count': len(successful),
        'failed_count': len(failed),
        'total_properties': total_properties,
        'results': results
    }
    with open('crawling_results.json', 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)
    print(f"\n💾 결과가 'crawling_results.json' 파일에 저장되었습니다.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
