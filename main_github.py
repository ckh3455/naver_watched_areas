#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 네이버 부동산 관심지역 크롤러 (GitHub Actions용, 2025-11 안정화)
# 개선: 매물탭 클릭 후 킥(scroll) + 리스트 컨테이너 우선 스크롤

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
        creds = service_account.Credentials.from_service_account_file(
            "service_account.json", scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        gc = gspread.authorize(creds)
        sheet_id = os.environ.get("SPREADSHEET_ID")
        ss = gc.open_by_key(sheet_id)
        try:
            ws = ss.worksheet("네이버 관심지역")
            print("✅ 구글 시트 연결 성공 (네이버 관심지역)")
        except gspread.WorksheetNotFound:
            ws = ss.add_worksheet("네이버 관심지역", rows=1000, cols=20)
            print("✅ 구글 시트 탭 생성 완료 (네이버 관심지역)")
        return ws
    except Exception as e:
        print(f"❌ 구글 시트 연결 실패: {e}")
        return None


class AggressiveCardScroll:
    def __init__(self, cid, cname):
        self.cid = cid
        self.cname = cname
        self.cards = []
        self.seen = set()

    async def _enter_page(self, page):
        urls = [
            f"https://new.land.naver.com/complexes/{self.cid}",
            f"https://new.land.naver.com/complexes/{self.cid}?a=APT&b=A1",
            f"https://new.land.naver.com/complexes/{self.cid}?ms=37.4779802,127.0413966,16&a=APT&b=A1&e=RETAIL",
        ]
        try:
            await page.goto("https://new.land.naver.com/", wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle", timeout=20000)
        except:
            pass
        for u in urls:
            try:
                r = await page.goto(u, wait_until="networkidle", timeout=60000)
                title = await page.title()
                print(f"  [enter] url='{page.url}' status={getattr(r,'status',None)} title='{title}'")
                if "/404" not in page.url and "부동산" in title:
                    return True
            except Exception as e:
                print(f"  [enter] fail: {e}")
        return False

    async def _focus_complex(self, page):
        if f"/complexes/{self.cid}" in page.url:
            return True
        name = self.cname
        try:
            search = page.locator('input[type="search"]').first
            if await search.count() > 0:
                await search.fill(name)
                await page.keyboard.press("Enter")
                await asyncio.sleep(1.0)
            item = page.locator(f'li:has-text("{name}")').first
            if await item.count() > 0:
                await item.click(timeout=3000)
                await page.wait_for_url(lambda u: f"/complexes/{self.cid}" in u, timeout=5000)
                return True
        except:
            pass
        try:
            link = page.locator(f'a[href*="/complexes/{self.cid}"]').first
            if await link.count() > 0:
                await link.click(timeout=3000)
                await page.wait_for_url(lambda u: f"/complexes/{self.cid}" in u, timeout=5000)
                return True
        except:
            pass
        return False

    async def _aggressive_warm_scroll(self, page):
        print("  ↪ 매물 탭 실패: 워밍업 스크롤")
        for _ in range(10):
            try:
                await page.keyboard.press("PageDown")
                await page.mouse.wheel(0, 1000)
            except:
                pass
            await asyncio.sleep(0.3)
        print("  ↪ 워밍업 종료")

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled", "--window-size=1920,1040"],
            )
            ctx = await browser.new_context(
                viewport={"width": 1920, "height": 1040},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0"),
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                extra_http_headers={"Referer": "https://land.naver.com/", "Accept-Language": "ko-KR,ko"},
            )

            page = await ctx.new_page()

            async def on_resp(resp):
                if "complexArticleList" in resp.url:
                    try:
                        js = await resp.json()
                        for it in js.get("result", {}).get("list", []):
                            aid = str(it.get("articleNo", ""))
                            if aid and aid not in self.seen:
                                self.seen.add(aid)
                                self.cards.append({"raw": it, "complex": self.cname})
                    except:
                        pass
            page.on("response", on_resp)

            if not await self._enter_page(page):
                print("  ❌ 진입 실패")
                await browser.close()
                return {"complex": self.cname, "count": 0, "props": []}

            if f"/complexes/{self.cid}" not in page.url:
                ok = await self._focus_complex(page)
                print(f"  [focus] {self.cname} → {'OK' if ok else 'FAIL'}")
                if not ok:
                    await browser.close()
                    return {"complex": self.cname, "count": 0, "props": []}

            # 탭 클릭
            tab_clicked = False
            for sel in [
                'a.complex_link span:has-text("매물")',
                'a:has-text("매물")',
                'button:has-text("매물")',
                '[role="tab"]:has-text("매물")',
            ]:
                try:
                    btn = page.locator(sel)
                    await btn.click(timeout=2000)
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    await asyncio.sleep(1.0)
                    await page.keyboard.press("PageDown")
                    await page.mouse.wheel(0, 800)
                    print(f"  ✓ 매물 탭 클릭 ({sel})")
                    tab_clicked = True
                    break
                except:
                    continue
            if not tab_clicked:
                await self._aggressive_warm_scroll(page)

            # 스크롤 루프
            list_candidates = ['#listContents', 'div[role="list"]', 'div[class*="list"]']
            no_new = 0
            for i in range(100):
                prev = len(self.cards)
                scrolled = False
                for sel in list_candidates:
                    try:
                        cnt = await page.locator(sel).count()
                        if cnt > 0:
                            for k in range(min(cnt, 2)):
                                loc = page.locator(sel).nth(k)
                                await loc.evaluate("(el)=>{el.scrollTop+=el.clientHeight*0.9;}")
                                scrolled = True
                            print(f"  ↪ 리스트 스크롤({sel})")
                            break
                    except:
                        pass
                if not scrolled:
                    try:
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    except:
                        pass
                await asyncio.sleep(1.5)
                cur = len(self.cards)
                if cur > prev:
                    no_new = 0
                    print(f"  📊 스크롤 {i+1}: {cur}개 매물")
                else:
                    no_new += 1
                if no_new >= 3:
                    print("  ✓ 스크롤 완료 (변화 없음)")
                    break

            await browser.close()
            return {"complex": self.cname, "count": len(self.cards), "props": self.cards}


def format_row(p):
    r = p.get("raw", {})
    t = r.get("tradeTypeName", "")
    price = r.get("dealOrWarrantPrc", "")
    if t == "월세":
        d, m = r.get("dealOrWarrantPrc", ""), r.get("rentPrc", "")
        price = f"{d}/{m}만원" if d and m else d or m
    return [
        p.get("complex", ""), t, r.get("buildingName", ""), r.get("floorInfo", ""),
        r.get("areaName", ""), price, "", 1,
        r.get("realtorName", ""), r.get("articleConfirmYmd", ""),
        r.get("articleFeatureDesc", ""), r.get("cpName", ""), r.get("articleNo", "")
    ]


async def main():
    print("="*60)
    print("🏢 네이버 부동산 크롤러 시작")
    print(f"⏰ {datetime.now():%Y-%m-%d %H:%M:%S}")
    ws = setup_google_sheets()
    if not ws:
        return
    ws.clear()
    ws.append_row(["단지명","거래구분","동","층수","면적","가격","가격변동","중복업소","중개업소","등록일자","특기사항","제공","매물번호"])
    props = []
    for i,c in enumerate(COMPLEXES,1):
        print(f"\n{'='*60}\n📍[{i}/{len(COMPLEXES)}] {c['name']}\n{'='*60}")
        t0=time.time()
        try:
            r=await AggressiveCardScroll(c["id"],c["name"]).run()
            print(f"✅ {c['name']}: {r['count']}개 ({time.time()-t0:.1f}s)")
            for p in r["props"]:
                props.append(format_row(p))
        except Exception as e:
            print(f"❌ {c['name']} 실패: {e}")
        await asyncio.sleep(5)
    if props:
        ws.append_rows(props)
        print(f"✅ 시트 기록 완료 ({len(props)}개)")
    else:
        print("⚠️ 매물 없음")
    with open("crawling_results.json","w",encoding="utf-8") as f:
        json.dump(props,f,ensure_ascii=False,indent=2)
    print("💾 결과 저장 완료")

if __name__=="__main__":
    asyncio.run(main())
