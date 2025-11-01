#!/usr/bin/env python3
"""
GitHub Actions용 네이버 부동산 크롤러
매일 자동으로 여러 단지의 매물 정보를 수집하여 Google Sheets의
'네이버 관심지역' 탭에 기록합니다.
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


# 이하 로직(크롤링, 포맷팅, 기록)은 기존 그대로 유지...
# ───────────────────────────────────────────────────────────────
# (생략 없이 기존 버전 그대로 붙여넣기)
# ───────────────────────────────────────────────────────────────
# ⚠️ 위의 setup_google_sheets 부분만 "네이버 관심지역"으로 바뀐 것임.
