#!/usr/bin/env python3
"""
GitHub Actions용 네이버 부동산 크롤러 (1번 기반 + 2번 추출방식 통합)

- 1번 스크립트: 구글시트/헤더/포맷/디버깅/단지목록/저장 구조 유지
- 2번 스크립트: "상세매물검색" 진입 + 리스트 컨테이너 스크롤 + isMoreData 기반 수집 방식 적용
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
import traceback
import urllib.parse

# 31개 단지 목록
COMPLEXES = [
    {"id": "3833", "name": "남산타운"},
    {"id": "950", "name": "행당대림"},
    {"id": "133962", "name": "상도역롯데캐슬파크엘"},
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
    {"id": "97", "name": "자양 우성7차"},
    {"id": "90", "name": "자양 우성1"},
    {"id": "93", "name": "자양우성3"},
    {"id": "92", "name": "자양 우성2차"},
    {"id": "75", "name": "광장현대5단지"},
    {"id": "101273", "name": "자연앤힐스테이트"},
    {"id": "101301", "name": "광교호수마을호반써밋"},
    {"id": "111038", "name": "광교중흥에스클래스(주상복합)"},
    {"id": "27508", "name": "판교푸르지오그랑블"},
    {"id": "3621", "name": "분당 파크뷰(주상복합)"},
    {"id": "107014", "name": "일산요진와이시티(주거복합)"},
    {"id": "113065", "name": "부천 센트럴파크푸르지오(주상복합)"},
    {"id": "122109", "name": "힐스테이트중동(주상복합)"},
    {"id": "107328", "name": "래미안부천중동"},
    {"id": "147880", "name": "안양역푸르지오더샵"},
    {"id": "175697", "name": "호계동 아크로베스티뉴"},
    {"id": "147229", "name": "힐스테이트구리역"},
    {"id": "119652", "name": "동탄역롯데캐슬(주상복합)"},
]

def debug_log(message, level="INFO"):
    """초상세 디버깅 로그"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    prefix = {
        "INFO": "ℹ️ ",
        "SUCCESS": "✅",
        "ERROR": "❌",
        "WARNING": "⚠️ ",
        "DEBUG": "🔍",
        "STEP": "➡️ "
    }.get(level, "")
    print(f"[{timestamp}] {prefix} {message}")

def setup_google_sheets():
    """구글 시트 설정 (1번 방식 유지: service_account.json 사용)"""
    debug_log("=== 구글 시트 설정 시작 ===", "STEP")

    try:
        credentials_file = 'service_account.json'
        debug_log(f"서비스 계정 파일 확인: {credentials_file}", "DEBUG")

        if not os.path.exists(credentials_file):
            debug_log(f"서비스 계정 파일이 없습니다: {credentials_file}", "ERROR")
            return None

        file_size = os.path.getsize(credentials_file)
        debug_log(f"서비스 계정 파일 크기: {file_size} bytes", "DEBUG")

        debug_log("서비스 계정 인증 시작...", "DEBUG")
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        debug_log("서비스 계정 인증 완료", "SUCCESS")

        service_email = credentials.service_account_email
        debug_log(f"🔑 사용 중인 서비스 계정: {service_email}", "INFO")

        debug_log("gspread 클라이언트 생성 중...", "DEBUG")
        gc = gspread.authorize(credentials)
        debug_log("gspread 클라이언트 생성 완료", "SUCCESS")

        spreadsheet_id = os.environ.get('SPREADSHEET_ID', '1FfeV5dkq7MTe443iMIYjztueWcUkv8ngsrDQmEzeTA4')
        debug_log(f"스프레드시트 ID: {spreadsheet_id}", "DEBUG")

        debug_log("스프레드시트 열기 시도...", "DEBUG")
        spreadsheet = gc.open_by_key(spreadsheet_id)
        debug_log(f"스프레드시트 열기 성공: {spreadsheet.title}", "SUCCESS")

        try:
            debug_log("'네이버 관심지역' 워크시트 찾기 시도...", "DEBUG")
            worksheet = spreadsheet.worksheet("네이버 관심지역")
            debug_log("기존 워크시트 발견", "SUCCESS")
            return worksheet
        except gspread.WorksheetNotFound:
            debug_log("워크시트 없음. 새로 생성 중...", "WARNING")
            worksheet = spreadsheet.add_worksheet(title="네이버 관심지역", rows=1000, cols=25)
            debug_log("워크시트 생성 완료", "SUCCESS")
            return worksheet

    except Exception as e:
        debug_log(f"구글 시트 설정 실패: {str(e)}", "ERROR")
        debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
        return None

# ====================================================================
# (2번 추출/포맷 유틸)
# ====================================================================

def _truthy(val):
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("true", "y", "1", "yes")

def _to_int(val, default=0):
    try:
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return int(val)
        s = str(val).strip().replace(",", "")
        return int(float(s))
    except Exception:
        return default

def _id_or_empty(val):
    if val is None:
        return ""
    s = str(val).strip()
    if not s:
        return ""
    s = s.strip().strip('"').strip("'")
    s = re.sub(r"[^A-Za-z0-9._-]", "", s)
    return s

def _to_text_cell(val):
    s = _id_or_empty(val)
    return f"'{s}" if s else ""

def _extract_realtor_id(raw):
    candidate_keys = (
        "realtorId", "realtorIdStr", "realtorNo", "realEstateAgentNo",
        "agentNo", "realtorIdNo", "agentId", "officeId"
    )
    for k in candidate_keys:
        if k in raw and raw[k]:
            rid = _id_or_empty(raw[k])
            if rid:
                return rid

    for sub in ("realtor", "realtorInfo", "agent", "office"):
        obj = raw.get(sub)
        if isinstance(obj, dict):
            for k in candidate_keys:
                if k in obj and obj[k]:
                    rid = _id_or_empty(obj[k])
                    if rid:
                        return rid

    url = (raw.get("realtorLinkUrl") or raw.get("realtorUrl") or "").strip()
    if url:
        try:
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            for qk in ("realtorId", "realtor_id", "agentId", "officeId"):
                if qk in qs and qs[qk]:
                    rid = urllib.parse.unquote(qs[qk][0])
                    rid = _id_or_empty(rid)
                    if rid:
                        return rid
        except Exception:
            pass
    return ""

def _has_photos(raw):
    for k in ("siteImageCount", "representativeImageCount", "imageCount"):
        if k in raw and _to_int(raw.get(k), 0) > 0:
            return True
    for k in ("siteImageCountYn", "representativeImageExistYn"):
        if k in raw and _truthy(raw.get(k)):
            return True
    return False

def _parse_price_number(s):
    if s is None:
        return None
    t = str(s).strip()
    try:
        if "억" in t:
            parts = t.replace(" ", "").split("억")
            eok = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            base = int(float(eok)) * 10000
            rest = rest.replace(",", "").replace("만", "")
            add = int(rest) if rest.isdigit() else 0
            return base + add
        return int(float(t.replace(",", "").replace("만", "")))
    except Exception:
        return None

def _resolve_price_change(raw):
    v = raw.get("priceChangeState")
    if isinstance(v, str) and v:
        updown = v.strip().upper()
        if updown == "UP":
            return "상승"
        if updown == "DOWN":
            return "하락"

    changed = _truthy(v)
    if not changed:
        return ""

    for k in ("priceChangeType", "dealPriceChangeTypeCode", "rentPriceChangeTypeCode", "priceChangeDirection"):
        s = raw.get(k)
        if isinstance(s, str):
            su = s.upper()
            if "UP" in su:
                return "상승"
            if "DOWN" in su:
                return "하락"

    for k in ("priceChange", "priceChangeAmount"):
        delta = raw.get(k)
        if delta is not None:
            try:
                d = float(str(delta).replace(",", ""))
                if d > 0:
                    return "상승"
                if d < 0:
                    return "하락"
            except Exception:
                pass

    prev_candidates = ("previousDealOrWarrantPrc", "prevPrice", "previousPrice")
    curr_candidates = ("dealOrWarrantPrc", "price", "currentPrice")

    prev_val = None
    curr_val = None

    for pk in prev_candidates:
        pv = _parse_price_number(raw.get(pk))
        if pv is not None:
            prev_val = pv
            break

    for ck in curr_candidates:
        cv = _parse_price_number(raw.get(ck))
        if cv is not None:
            curr_val = cv
            break

    if prev_val is not None and curr_val is not None and prev_val != curr_val:
        return "상승" if curr_val > prev_val else "하락"

    return "변동"

# ====================================================================
# 2번 방식(컨테이너 스크롤 + isMoreData) 적용 크롤러
# ====================================================================

class AggressiveCardScroll:
    """네이버 부동산 매물 크롤러 (2번 추출 방식 적용)"""

    def __init__(self, complex_id, complex_name):
        debug_log(f"크롤러 초기화: {complex_name} (ID: {complex_id})", "DEBUG")
        self.complex_id = complex_id
        self.complex_name = complex_name
        self.base_url = f"https://new.land.naver.com/complexes/{self.complex_id}"
        self.property_cards = []
        self.unique_article_nos = set()
        self.page = None
        self.api_responses = []
        self.more_data = True
        debug_log(f"크롤러 초기화 완료. URL: {self.base_url}", "SUCCESS")

    async def setup_playwright(self):
        """Playwright 환경 설정"""
        debug_log("=== Playwright 환경 설정 시작 ===", "STEP")

        try:
            self.playwright = await async_playwright().start()

            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--window-size=1920,1080'
                ]
            )

            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                extra_http_headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }
            )

            self.page = await self.context.new_page()
            self.page.on('response', self.handle_response)
            debug_log("Playwright 설정 완료 + 응답 리스너 등록", "SUCCESS")

        except Exception as e:
            debug_log(f"Playwright 설정 실패: {str(e)}", "ERROR")
            debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
            raise

    async def handle_response(self, response):
        """매물 관련 API 응답만 저장/추출"""
        url = response.url
        if 'api/articles/complex' in url and 'page=' in url:
            try:
                debug_log(f"API 응답 감지: {url}", "DEBUG")
                data = await response.json()
                self.api_responses.append({
                    'url': url,
                    'data': data,
                    'timestamp': datetime.now().isoformat()
                })
                await self.extract_properties_from_response(data, url)
            except Exception as e:
                debug_log(f"API 응답 파싱 실패: {url} - {str(e)}", "WARNING")

    def _extract_article_list(self, data):
        """(안전장치) articleList 구조 변화 대응"""
        if not isinstance(data, dict):
            return []
        if isinstance(data.get("articleList"), list):
            return data.get("articleList") or []
        for path in (("body", "articleList"), ("result", "articleList"), ("data", "articleList")):
            cur = data
            ok = True
            for k in path:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                else:
                    ok = False
                    break
            if ok and isinstance(cur, list):
                return cur
        return []

    async def extract_properties_from_response(self, data, url):
        """API 응답에서 매물 데이터 추출 + isMoreData 갱신"""
        articles = self._extract_article_list(data)
        if not articles:
            return False

        page_match = re.search(r'page=(\d+)', url)
        page_num = page_match.group(1) if page_match else "Unknown"

        debug_log(f"페이지 {page_num}에서 {len(articles)}개 매물 발견", "INFO")

        new_properties = 0
        for article in articles:
            if isinstance(article, dict):
                article_no = article.get('articleNo', '')
                if article_no and article_no not in self.unique_article_nos:
                    self.unique_article_nos.add(article_no)

                    is_owner = article.get('verificationTypeCode') == 'OWNER'

                    property_data = {
                        'complex_id': self.complex_id,
                        'complex_name': self.complex_name,
                        'article_no': article_no,
                        'raw_data': article,
                        'extracted_at': datetime.now().isoformat(),
                        'card_number': len(self.property_cards) + 1,
                        'is_owner_flag': is_owner,
                        'page_number': page_num
                    }
                    self.property_cards.append(property_data)
                    new_properties += 1

        if new_properties > 0:
            debug_log(f"  ➕ 페이지 {page_num}에서 {new_properties}개 매물 추가 (총 {len(self.property_cards)}개)", "SUCCESS")

        # 2번 방식: isMoreData 기반 루프 컨트롤
        if isinstance(data, dict) and "isMoreData" in data:
            self.more_data = bool(data.get("isMoreData", False))
            debug_log(f"  📄 isMoreData: {self.more_data}", "DEBUG")
        else:
            # 안전장치: isMoreData가 없으면 페이지사이즈 기반(보통 20)으로 추정
            self.more_data = (new_properties > 0 and len(articles) >= 20)
            debug_log(f"  📄 isMoreData 없음 → 휴리스틱 more_data={self.more_data}", "DEBUG")

        return self.more_data

    async def navigate_to_complex_page(self):
        """단지 페이지로 이동 + 상세매물검색 진입(2번 방식)"""
        debug_log("=== 단지 페이지 이동 시작 ===", "STEP")
        debug_log(f"대상 URL: {self.base_url}", "INFO")

        try:
            await self.page.goto(self.base_url, wait_until='domcontentloaded', timeout=60000)
            debug_log("페이지 로드 완료(domcontentloaded)", "SUCCESS")
            await asyncio.sleep(3)

            title = await self.page.title()
            debug_log(f"페이지 제목: {title}", "INFO")

            # 2번 방식: "상세매물검색" 버튼 클릭 시도
            debug_log("상세매물검색 버튼 클릭 시도...", "DEBUG")
            try:
                clicked = await self.page.evaluate("""
                    () => {
                        const btn = Array.from(document.querySelectorAll('button'))
                          .find(b => (b.innerText || '').includes('상세매물검색'));
                        if (btn) { btn.click(); return true; }
                        return false;
                    }
                """)
                if clicked:
                    debug_log("✅ 상세매물검색 버튼 클릭 성공", "SUCCESS")
                    await asyncio.sleep(2)
                else:
                    debug_log("⚠️ 상세매물검색 버튼을 찾지 못함 (이미 활성화/구조변경 가능)", "WARNING")
            except Exception as e:
                debug_log(f"상세매물검색 버튼 클릭 중 오류: {str(e)}", "WARNING")

            # 최초 매물 API 한번 기다려 보기(있으면 바로 캐치됨)
            try:
                await self.page.wait_for_response(
                    lambda r: ('api/articles/complex' in r.url and 'page=' in r.url),
                    timeout=5000
                )
                debug_log("📡 초기 매물 API 응답 감지", "SUCCESS")
            except Exception:
                debug_log("⚠️ 초기 매물 API 응답 대기 타임아웃(스크롤로 유도 예정)", "WARNING")

            debug_log(f"현재 URL: {self.page.url}", "INFO")

        except Exception as e:
            debug_log(f"페이지 로드 실패: {str(e)}", "ERROR")
            debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
            raise

    async def aggressive_scroll(self):
        """2번 방식: 매물 리스트 컨테이너 스크롤 + isMoreData 기반 반복"""
        debug_log("=== 스크롤 컨테이너 방식 스크롤 시작 ===", "STEP")

        last_height = 0
        no_height_change_count = 0
        MAX_NO_CHANGE = 3
        scroll_attempts = 0

        # 컨테이너 셀렉터 후보(AB 테스트/클래스 변경 대비)
        container_selectors = [
            ".item_list--article",
            "[class*='item_list'][class*='article']",
            ".item_list",
            "[data-testid*='article']",
        ]

        while self.more_data:
            prev_count = len(self.property_cards)

            # 컨테이너 상태 조회
            try:
                container_state = await self.page.evaluate(
                    """(selectors) => {
                        const pick = () => {
                          for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el && el.scrollHeight && el.clientHeight) return {sel, el};
                          }
                          return null;
                        };
                        const p = pick();
                        if (!p) return { success: false };
                        const el = p.el;
                        return {
                            success: true,
                            selector: p.sel,
                            scrollHeight: el.scrollHeight,
                            scrollTop: el.scrollTop,
                            clientHeight: el.clientHeight
                        };
                    }""",
                    container_selectors
                )

                if not container_state.get("success"):
                    debug_log("⚠️ 스크롤 컨테이너를 찾지 못함 → 스크롤 종료", "WARNING")
                    break

                sel = container_state.get("selector")
                current_height = container_state['scrollHeight']
                is_at_bottom = (container_state['scrollTop'] + container_state['clientHeight']) >= (current_height - 10)

                if current_height == last_height and is_at_bottom:
                    no_height_change_count += 1
                    debug_log(f"⚠️ 컨테이너 높이 변화 없음 ({no_height_change_count}/{MAX_NO_CHANGE}) sel={sel}", "WARNING")

                    if no_height_change_count >= MAX_NO_CHANGE:
                        if not self.more_data:
                            debug_log("⏹️ isMoreData=False + 높이 변화 없음 → 종료", "INFO")
                            break
                        else:
                            debug_log("⚠️ 높이 변화 없지만 isMoreData=True → 8초 추가 대기 후 재시도", "WARNING")
                            await asyncio.sleep(8)
                            no_height_change_count = 0
                else:
                    no_height_change_count = 0
                    if current_height > last_height:
                        debug_log(f"📈 컨테이너 확장: {last_height} → {current_height} (+{current_height - last_height}px) sel={sel}", "SUCCESS")
                    last_height = current_height

                # 실제 스크롤 수행
                scroll_result = await self.page.evaluate(
                    """(selectors) => {
                        const pick = () => {
                          for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el && el.scrollHeight && el.clientHeight) return el;
                          }
                          return null;
                        };
                        const el = pick();
                        if (!el) return { success: false };
                        const before = el.scrollTop;
                        el.scrollTop += 1200;
                        const after = el.scrollTop;
                        const denom = (el.scrollHeight - el.clientHeight) || 1;
                        return {
                            success: true,
                            scrolled: after - before,
                            progress: ((after / denom) * 100).toFixed(1)
                        };
                    }""",
                    container_selectors
                )

                scroll_attempts += 1
                if scroll_result.get("success"):
                    debug_log(f"🔄 [{scroll_attempts}] 스크롤: {scroll_result['scrolled']}px (진행률: {scroll_result['progress']}%)", "DEBUG")
                else:
                    debug_log(f"⚠️ [{scroll_attempts}] 스크롤 실패", "WARNING")

                # 응답 대기(있으면 빠르게 반응)
                try:
                    await self.page.wait_for_response(
                        lambda r: ('api/articles/complex' in r.url and 'page=' in r.url),
                        timeout=3000
                    )
                    debug_log("📡 API 응답 감지!", "SUCCESS")
                except Exception:
                    pass

                await asyncio.sleep(1.2)

                if len(self.property_cards) > prev_count:
                    added = len(self.property_cards) - prev_count
                    debug_log(f"🎉 새 매물 {added}개 추가! (총 {len(self.property_cards)}개)", "SUCCESS")

                # 안전중단
                if scroll_attempts >= 180:
                    debug_log(f"⛔ 안전중단: 180회 스크롤 시도 초과 (총 {len(self.property_cards)}개 수집)", "WARNING")
                    break

            except Exception as e:
                debug_log(f"스크롤 중 오류: {str(e)}", "WARNING")
                await asyncio.sleep(2)

        debug_log(f"✅ 스크롤 완료 (총 {len(self.property_cards)}개 수집, {scroll_attempts}회 시도)", "SUCCESS")

    async def close_browser(self):
        debug_log("브라우저 종료 중...", "DEBUG")
        try:
            if hasattr(self, 'browser'):
                await self.browser.close()
                debug_log("브라우저 종료 완료", "SUCCESS")
            if hasattr(self, 'playwright'):
                await self.playwright.stop()
                debug_log("Playwright 종료 완료", "SUCCESS")
        except Exception as e:
            debug_log(f"브라우저 종료 중 오류: {str(e)}", "WARNING")

    async def run(self):
        """크롤러 실행"""
        debug_log(f"\n{'='*70}", "STEP")
        debug_log(f"🏢 {self.complex_name} 크롤링 시작", "STEP")
        debug_log(f"{'='*70}", "STEP")

        try:
            await self.setup_playwright()
            await self.navigate_to_complex_page()
            await self.aggressive_scroll()

            debug_log("\n📊 수집 완료 요약:", "STEP")
            debug_log(f"  - 단지: {self.complex_name} (ID: {self.complex_id})", "INFO")
            debug_log(f"  - 고유 매물: {len(self.property_cards)}개", "INFO")
            debug_log(f"  - API 응답: {len(self.api_responses)}개", "INFO")

            await self.close_browser()

            return {
                'complex_id': self.complex_id,
                'complex_name': self.complex_name,
                'property_count': len(self.property_cards),
                'api_responses': len(self.api_responses),
                'properties': self.property_cards
            }

        except Exception as e:
            debug_log(f"크롤링 중 치명적 오류 발생: {str(e)}", "ERROR")
            debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
            await self.close_browser()
            raise

# ====================================================================
# 21열 포맷(1번 유지)
# ====================================================================

def format_property_data(property_data):
    """
    21열 반환

    [단지명, 거래구분, 동, 층수, 면적, 가격,
     가격변동, 중복업소,
     중개업소, 중개업소ID,
     등록일자, 방향, 특기사항,
     제공, 집주인, 직거래,
     사진 유무, 경도, 위도, 매물번호,
     인증광고]
    """
    raw_data = property_data.get('raw_data', {})

    # 면적
    area1 = raw_data.get('area1', '')
    area2 = raw_data.get('area2', '')
    if area1 and area2 and area1 != area2:
        area = f"{area1}/{area2}m²"
    elif area1:
        area = f"{area1}m²"
    else:
        area = (raw_data.get('areaName', '') + "m²") if raw_data.get('areaName') else "Unknown"

    # 가격
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

    # 가격변동
    price_change_display = _resolve_price_change(raw_data)

    # 집주인/직거래/사진/인증광고
    is_owner_listing = "집주인" if property_data.get('is_owner_flag') is True else ""
    certification_ad = "인증광고" if _truthy(raw_data.get('tradeCheckedByOwner')) else ""
    direct_trade_listing = "직거래" if _truthy(raw_data.get('isDirectTrade')) else ""
    photo_status = "사진있음" if _has_photos(raw_data) else ""

    # 등록일
    date_str = raw_data.get('articleConfirmYmd', '')
    if date_str and len(str(date_str)) == 8 and str(date_str).isdigit():
        registration_date = f"{str(date_str)[:4]}.{str(date_str)[4:6]}.{str(date_str)[6:8]}"
    else:
        registration_date = date_str or "Unknown"

    # 중개업소ID (텍스트 강제)
    realtor_id_raw = _extract_realtor_id(raw_data)
    realtor_id_cell = _to_text_cell(realtor_id_raw)

    return [
        property_data.get('complex_name', ''),               # 단지명
        trade_type,                                          # 거래구분
        raw_data.get('buildingName', ''),                    # 동
        raw_data.get('floorInfo', ''),                       # 층수
        area,                                                # 면적
        price,                                               # 가격
        price_change_display,                                # 가격변동
        1,                                                   # 중복업소 (기존 로직 유지)
        raw_data.get('realtorName', 'Unknown'),              # 중개업소
        realtor_id_cell,                                     # 중개업소ID
        registration_date,                                   # 등록일자
        raw_data.get('direction', ''),                       # 방향
        raw_data.get('articleFeatureDesc', ''),              # 특기사항
        raw_data.get('cpName', 'Unknown'),                   # 제공
        is_owner_listing,                                    # 집주인
        direct_trade_listing,                                # 직거래
        photo_status,                                        # 사진 유무
        raw_data.get('longitude', ''),                       # 경도
        raw_data.get('latitude', ''),                        # 위도
        raw_data.get('articleNo', ''),                       # 매물번호
        certification_ad                                     # 인증광고
    ]

# ====================================================================
# main (1번 유지)
# ====================================================================

async def main():
    print("\n" + "="*70)
    print("🚀 네이버 부동산 크롤러 시작 (1번 기반 + 2번 추출방식)")
    print(f"⏰ 시작시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70 + "\n")

    # 구글 시트 연결
    debug_log("=== 1단계: 구글 시트 연결 ===", "STEP")
    worksheet = setup_google_sheets()
    if not worksheet:
        debug_log("구글 시트 연결 실패. 프로그램 종료", "ERROR")
        return

    # 기존 데이터 삭제 및 헤더 추가
    debug_log("=== 2단계: 시트 초기화 ===", "STEP")
    worksheet.clear()
    debug_log("기존 데이터 삭제 완료", "SUCCESS")

    headers = [
        "단지명", "거래구분", "동", "층수", "면적", "가격",
        "가격변동", "중복업소",
        "중개업소", "중개업소ID",
        "등록일자", "방향", "특기사항",
        "제공", "집주인", "직거래",
        "사진 유무", "경도", "위도", "매물번호",
        "인증광고"
    ]
    worksheet.append_row(headers)
    debug_log("헤더 추가 완료", "SUCCESS")

    # 크롤링 시작
    debug_log("=== 3단계: 크롤링 실행 ===", "STEP")
    results = []
    all_properties = []
    total_start_time = time.time()

    for idx, complex_info in enumerate(COMPLEXES, 1):
        debug_log(f"\n{'#'*70}", "STEP")
        debug_log(f"📍 진행: [{idx}/{len(COMPLEXES)}] {complex_info['name']}", "STEP")
        debug_log(f"{'#'*70}", "STEP")

        complex_start_time = time.time()

        try:
            crawler = AggressiveCardScroll(complex_info['id'], complex_info['name'])
            result = await crawler.run()

            complex_duration = time.time() - complex_start_time
            property_count = result['property_count']
            debug_log(f"✅ {complex_info['name']} 완료: {property_count}개 매물 ({complex_duration:.1f}초)", "SUCCESS")

            if result.get('properties'):
                debug_log(f"데이터 포맷팅 중... ({len(result['properties'])}개)", "DEBUG")
                for property_data in result['properties']:
                    formatted_row = format_property_data(property_data)
                    all_properties.append(formatted_row)
                debug_log("데이터 포맷팅 완료", "SUCCESS")

            results.append({
                'complex_name': complex_info['name'],
                'property_count': property_count,
                'duration_seconds': complex_duration,
                'status': 'success'
            })

        except Exception as e:
            complex_duration = time.time() - complex_start_time
            debug_log(f"❌ {complex_info['name']} 실패: {str(e)} ({complex_duration:.1f}초)", "ERROR")
            debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")

            results.append({
                'complex_name': complex_info['name'],
                'property_count': 0,
                'duration_seconds': complex_duration,
                'status': 'error',
                'error': str(e)
            })

        if idx < len(COMPLEXES):
            debug_log("다음 단지까지 5초 대기...", "DEBUG")
            await asyncio.sleep(5)

    # 구글 시트에 데이터 기록
    debug_log("=== 4단계: 구글 시트 기록 ===", "STEP")
    if all_properties:
        debug_log(f"총 {len(all_properties)}개 매물 기록 중...", "INFO")
        worksheet.append_rows(all_properties)
        debug_log("구글 시트 기록 완료", "SUCCESS")
    else:
        debug_log("기록할 매물 데이터가 없습니다", "WARNING")

    # 전체 결과 요약
    total_duration = time.time() - total_start_time

    successful = [r for r in results if r['status'] == 'success']
    failed = [r for r in results if r['status'] == 'error']
    total_properties = sum(r['property_count'] for r in results)

    print("\n" + "="*70)
    print("📊 전체 결과 요약")
    print("="*70)
    print(f"⏰ 종료시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⏱️  전체 소요시간: {total_duration:.1f}초 ({total_duration/60:.1f}분)")
    print(f"✅ 성공한 단지: {len(successful)}개")
    print(f"❌ 실패한 단지: {len(failed)}개")
    print(f"🏠 총 매물 수: {total_properties}개")
    print("="*70)

    print("\n📋 단지별 상세 결과:")
    print("-"*70)
    for i, result in enumerate(results, 1):
        status_icon = "✅" if result['status'] == 'success' else "❌"
        print(f"{i:2d}. {status_icon} {result['complex_name']:20s} | {result['property_count']:4d}개 | {result['duration_seconds']:5.1f}초")
    print("-"*70)

    # 결과를 JSON 파일로 저장
    debug_log("=== 5단계: 결과 파일 저장 ===", "STEP")
    result_data = {
        'total_duration_seconds': total_duration,
        'total_duration_minutes': total_duration/60,
        'start_time': datetime.fromtimestamp(total_start_time).strftime('%Y-%m-%d %H:%M:%S'),
        'end_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'successful_count': len(successful),
        'failed_count': len(failed),
        'total_properties': total_properties,
        'results': results
    }

    with open('crawling_results.json', 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    debug_log("결과 파일 저장 완료: crawling_results.json", "SUCCESS")

    print("\n" + "="*70)
    print("🎉 크롤링 완료!")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
