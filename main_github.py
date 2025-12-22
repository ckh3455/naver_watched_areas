#!/usr/bin/env python3
"""
GitHub Actions용 네이버 부동산 크롤러 (검증된 최종버전)
- 22개 단지의 매물 데이터를 안정적으로 크롤링하여 Google Sheets에 저장
- 2단계 로그 시스템: 상세 로그 + 핵심 결과 요약
- GitHub Actions 환경에서 안정적으로 실행
- 크롤링 사이트 구조 변화 대응
"""

import asyncio
import json
import time
import gspread
import os
import sys
import re
import traceback
import urllib.parse
from datetime import datetime
from playwright.async_api import async_playwright
from google.oauth2.service_account import Credentials

# =========================
# 설정값
# =========================

# 크롤링 대상 단지 리스트 (22개) - 사용자 환경에 맞게 유지
COMPLEX_LIST = [
    {"id": "109884", "name": "래미안대치팰리스"},
    {"id": "111515", "name": "대치삼성"},
    {"id": "110272", "name": "은마"},
    {"id": "111434", "name": "삼익"},
    {"id": "110209", "name": "현대"},
    {"id": "110387", "name": "미도"},
    {"id": "110240", "name": "우성"},
    {"id": "110303", "name": "쌍용"},
    {"id": "110292", "name": "선경"},
    {"id": "110315", "name": "동부센트레빌"},
    {"id": "109866", "name": "대치SKVIEW"},
    {"id": "109832", "name": "래미안개포루체하임"},
    {"id": "110316", "name": "디에이치아너힐즈"},
    {"id": "110317", "name": "래미안블레스티지"},
    {"id": "110319", "name": "디에이치포레센트"},
    {"id": "110320", "name": "개포자이프레지던스"},
    {"id": "110321", "name": "자곡힐스테이트"},
    {"id": "110322", "name": "강남한양수자인"},
    {"id": "110323", "name": "강남데시앙포레"},
    {"id": "110324", "name": "디에이치자이개포"},
    {"id": "110325", "name": "개포래미안포레스트"},
    {"id": "110326", "name": "개포주공1단지"},
]

# 거래 유형 코드 (네이버 부동산)
TRADE_TYPE_CODES = [
    {"code": "A1", "name": "매매"},
    {"code": "B1", "name": "전세"},
    {"code": "B2", "name": "월세"},
]

# Google Sheets 설정
SPREADSHEET_NAME = "네이버부동산-크롤링"
WORKSHEET_NAME = "매물데이터"

# 크롤링 설정
MAX_PAGES_PER_TRADE_TYPE = 20  # 거래유형별 최대 페이지
DELAY_BETWEEN_REQUESTS = 0.7   # 요청 간 딜레이(초)
MAX_RETRY = 3                  # 재시도 횟수
HEADLESS = True                # GitHub Actions에서는 True

# =========================
# 유틸/로그
# =========================

LOG_LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "SUCCESS": 1, "STEP": 1}
CURRENT_LOG_LEVEL = "DEBUG"


def debug_log(message, level="INFO"):
    """콘솔 로그 출력 (레벨 기반)"""
    if LOG_LEVELS.get(level, 1) >= LOG_LEVELS.get(CURRENT_LOG_LEVEL, 0):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"[{level}]"
        print(f"{ts} {prefix} {message}")


def summarize_results(all_results):
    """크롤링 결과 요약 출력"""
    summary = {}
    for r in all_results:
        complex_name = r.get("complex_name", "Unknown")
        trade_type = r.get("trade_type", "Unknown")
        cnt = r.get("count", 0)
        summary.setdefault(complex_name, {})
        summary[complex_name].setdefault(trade_type, 0)
        summary[complex_name][trade_type] += cnt

    print("\n" + "=" * 70)
    print("크롤링 결과 요약")
    print("=" * 70)
    total = 0
    for complex_name, trade_map in summary.items():
        print(f"\n[{complex_name}]")
        for tt, c in trade_map.items():
            print(f"  - {tt}: {c}개")
            total += c
    print("\n" + "-" * 70)
    print(f"전체 합계: {total}개")
    print("=" * 70 + "\n")


# =========================
# 크롤러 클래스
# =========================

class NaverCrawler:
    """네이버 부동산 크롤러"""

    def __init__(self, complex_id, complex_name):
        self.complex_id = complex_id
        self.complex_name = complex_name
        self.property_cards = []
        self.unique_article_nos = set()
        self.browser = None
        self.page = None

    async def init_browser(self):
        """Playwright 브라우저 초기화"""
        debug_log("브라우저 초기화 중...", "DEBUG")
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(headless=HEADLESS)
        context = await self.browser.new_context()
        self.page = await context.new_page()

        # 네이버 부동산 페이지 접속 (쿠키/세션 확보)
        url = f"https://new.land.naver.com/complexes/{self.complex_id}"
        debug_log(f"네이버 부동산 접속: {url}", "DEBUG")
        await self.page.goto(url, wait_until="networkidle")
        await asyncio.sleep(2.0)

    async def close_browser(self):
        """브라우저 종료"""
        if self.browser:
            debug_log("브라우저 종료 중...", "DEBUG")
            await self.browser.close()

    async def fetch_api(self, url, headers=None):
        """API 요청 (재시도 포함)"""
        for attempt in range(1, MAX_RETRY + 1):
            try:
                debug_log(f"API 요청 시도 {attempt}/{MAX_RETRY}: {url}", "DEBUG")
                resp = await self.page.request.get(url, headers=headers)
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                data = await resp.json()
                return data
            except Exception as e:
                debug_log(f"API 요청 실패({attempt}): {str(e)}", "WARNING")
                if attempt == MAX_RETRY:
                    debug_log(f"API 요청 최종 실패: {url}", "ERROR")
                    return None
                await asyncio.sleep(1.0 + attempt * 0.5)
        return None

    async def extract_properties_from_response(self, data, url):
        """API 응답에서 매물 데이터 추출 (검증된 로직)"""
        if isinstance(data, dict) and 'articleList' in data:
            articles = data['articleList']
            page_match = re.search(r'page=(\d+)', url)
            page_num = page_match.group(1) if page_match else "Unknown"

            debug_log(f"페이지 {page_num}에서 {len(articles)}개 매물 발견", "INFO")

            new_properties = 0
            for article in articles:
                if isinstance(article, dict):
                    article_no = article.get('articleNo', '')
                    if article_no and article_no not in self.unique_article_nos:
                        self.unique_article_nos.add(article_no)

                        # 두번째 파일 추출구조: 집주인(OWNER) 여부 플래그
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

                        # 주요 정보 추출
                        dong = article.get('buildingName', '')
                        trade_type = article.get('tradeTypeName', '')
                        price = article.get('dealOrWarrantPrc', '')

                        debug_log(f"  새 매물 #{len(self.property_cards)} (번호: {article_no}): {dong} - {trade_type} {price}", "DEBUG")

            if new_properties > 0:
                debug_log(f"  ➕ {new_properties}개 새 매물 추가됨 (총 {len(self.property_cards)}개)", "SUCCESS")

            # isMoreData 확인
            is_more = data.get('isMoreData', False)
            return is_more
        return False

    async def crawl_trade_type(self, trade_type_code, trade_type_name):
        """특정 거래유형(A1/B1/B2)에 대한 전체 페이지 크롤링"""
        debug_log(f"[{self.complex_name}] 거래유형 {trade_type_name} 크롤링 시작", "STEP")

        base_url = "https://new.land.naver.com/api/articles/complex"
        headers = {
            "accept": "application/json",
            "referer": f"https://new.land.naver.com/complexes/{self.complex_id}",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        }

        total_before = len(self.property_cards)

        for page in range(1, MAX_PAGES_PER_TRADE_TYPE + 1):
            url = (
                f"{base_url}/{self.complex_id}"
                f"?realEstateType=APT%3AABYG%3AJGC"
                f"&tradeType={trade_type_code}"
                f"&tag=%3A"
                f"&rentPriceMin=0&rentPriceMax=900000000"
                f"&priceMin=0&priceMax=900000000"
                f"&areaMin=0&areaMax=900000000"
                f"&showArticle=false"
                f"&sameAddressGroup=false"
                f"&page={page}"
            )

            data = await self.fetch_api(url, headers=headers)
            if data is None:
                debug_log(f"페이지 {page} 데이터 없음/실패 - 중단", "WARNING")
                break

            try:
                is_more = await self.extract_properties_from_response(data, url)
            except Exception as e:
                debug_log(f"API 응답 파싱 실패: {url} - {str(e)}", "WARNING")
                continue

            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

            if not is_more:
                debug_log(f"추가 페이지 없음 (isMoreData=False) - 거래유형 {trade_type_name} 종료", "INFO")
                break

        total_after = len(self.property_cards)
        added = total_after - total_before
        debug_log(f"[{self.complex_name}] 거래유형 {trade_type_name} 완료: {added}개 추가", "SUCCESS")
        return added

    async def crawl_all(self):
        """단지 전체 크롤링: 거래유형별 순회"""
        try:
            await self.init_browser()
            results = []
            for t in TRADE_TYPE_CODES:
                code = t["code"]
                name = t["name"]
                added = await self.crawl_trade_type(code, name)
                results.append({
                    "complex_id": self.complex_id,
                    "complex_name": self.complex_name,
                    "trade_type": name,
                    "count": added
                })
            return results, self.property_cards
        except Exception as e:
            debug_log(f"크롤링 중 치명적 오류 발생: {str(e)}", "ERROR")
            debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
            await self.close_browser()
            raise


# =========================
# 파싱/보정 헬퍼 (두번째 파일 추출구조)
# =========================
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


def format_property_data(property_data):
    raw_data = property_data.get('raw_data', {})
    area1 = raw_data.get('area1', '')
    area2 = raw_data.get('area2', '')
    if area1 and area2 and area1 != area2:
        area = f"{area1}/{area2}m²"
    elif area1:
        area = f"{area1}m²"
    else:
        area = raw_data.get('areaName', '') + "m²" or "Unknown"

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

    price_change_display = _resolve_price_change(raw_data)

    is_owner_listing = "집주인" if property_data.get('is_owner_flag') is True else ""
    certification_ad = "인증광고" if _truthy(raw_data.get('tradeCheckedByOwner')) else ""
    direct_trade_listing = "직거래" if _truthy(raw_data.get('isDirectTrade')) else ""
    photo_status = "사진있음" if _has_photos(raw_data) else ""

    date_str = raw_data.get('articleConfirmYmd', '')
    if date_str and len(str(date_str)) == 8 and str(date_str).isdigit():
        registration_date = f"{str(date_str)[:4]}.{str(date_str)[4:6]}.{str(date_str)[6:8]}"
    else:
        registration_date = date_str or "Unknown"

    realtor_id_raw = _extract_realtor_id(raw_data)
    realtor_id_cell = _to_text_cell(realtor_id_raw)

    return [
        property_data.get('complex_name', ''),
        trade_type,
        raw_data.get('buildingName', ''),
        raw_data.get('floorInfo', ''),
        area,
        price,
        price_change_display,
        1,
        raw_data.get('realtorName', 'Unknown'),
        realtor_id_cell,
        registration_date,
        raw_data.get('direction', ''),
        raw_data.get('articleFeatureDesc', ''),
        raw_data.get('cpName', 'Unknown'),
        is_owner_listing,
        direct_trade_listing,
        photo_status,
        raw_data.get('longitude', ''),
        raw_data.get('latitude', ''),
        raw_data.get('articleNo', ''),
        certification_ad
    ]


async def main():
    """메인 실행 함수"""
    print("\n" + "="*70)
    print("네이버 부동산 크롤링 시작")
    print("="*70 + "\n")

    # 1) Google Sheets 인증
    debug_log("=== 1단계: Google Sheets 인증 ===", "STEP")

    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        debug_log("환경변수 GOOGLE_CREDENTIALS_JSON가 없습니다.", "ERROR")
        sys.exit(1)

    creds_info = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    gc = gspread.authorize(creds)
    debug_log("Google Sheets 인증 성공", "SUCCESS")

    # 2) 시트 준비
    debug_log("스프레드시트 열기 중...", "DEBUG")
    sh = gc.open(SPREADSHEET_NAME)
    worksheet = sh.worksheet(WORKSHEET_NAME)
    debug_log("스프레드시트/워크시트 열기 성공", "SUCCESS")

    # 기존 데이터 삭제 및 헤더 추가
    debug_log("=== 2단계: 시트 초기화 ===", "STEP")
    debug_log("기존 데이터 삭제 중...", "DEBUG")
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
    debug_log(f"헤더 추가 중: {headers}", "DEBUG")
    worksheet.append_row(headers)
    debug_log("헤더 추가 완료", "SUCCESS")

    # 크롤링 시작
    debug_log("=== 3단계: 크롤링 실행 ===", "STEP")
    results = []
    all_properties = []
    total_start_time = time.time()

    # 22개 단지 순회
    for idx, complex_info in enumerate(COMPLEX_LIST, start=1):
        cid = complex_info["id"]
        cname = complex_info["name"]
        debug_log(f"\n[{idx}/{len(COMPLEX_LIST)}] 단지 크롤링 시작: {cname} (ID: {cid})", "STEP")

        crawler = NaverCrawler(cid, cname)
        try:
            r, props = await crawler.crawl_all()
            results.extend(r)
            all_properties.extend(props)
        except Exception as e:
            debug_log(f"단지 크롤링 실패: {cname} - {str(e)}", "ERROR")
        finally:
            await crawler.close_browser()

    # 시트에 데이터 저장
    debug_log("=== 4단계: 시트 저장 ===", "STEP")
    debug_log(f"총 {len(all_properties)}개 매물 시트 저장 시작", "INFO")

    # 배치로 쌓아서 append (속도/쿼터 안정성)
    batch = []
    batch_size = 200
    saved = 0

    for p in all_properties:
        row = format_property_data(p)
        batch.append(row)
        if len(batch) >= batch_size:
            worksheet.append_rows(batch, value_input_option="USER_ENTERED")
            saved += len(batch)
            debug_log(f"  ✅ {saved}/{len(all_properties)} 저장 완료", "INFO")
            batch = []
            await asyncio.sleep(0.5)

    if batch:
        worksheet.append_rows(batch, value_input_option="USER_ENTERED")
        saved += len(batch)
        debug_log(f"  ✅ {saved}/{len(all_properties)} 저장 완료", "INFO")

    total_elapsed = time.time() - total_start_time
    debug_log(f"전체 크롤링/저장 완료. 총 소요시간: {total_elapsed:.1f}초", "SUCCESS")

    # 결과 요약 출력
    summarize_results(results)


if __name__ == "__main__":
    asyncio.run(main())
