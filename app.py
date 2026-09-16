import streamlit as st
import pandas as pd
import datetime
import calendar
from ortools.sat.python import cp_model
from streamlit_gsheets import GSheetsConnection

# 페이지 기본 설정
st.set_page_config(page_title="구글 시트 연동 근무표 생성기", layout="wide")
st.title("📊 구글 시트 연동 병동 근무표 자동 생성기")

# --- 1. 구글 시트 URL 입력 받기 ---
st.sidebar.header("🔗 구글 시트 연동")
sheet_url = st.sidebar.text_input(
    "구글 시트 URL 주소를 입력하세요:",
    value="https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"
)

# 구글 시트 연결 객체 생성
conn = st.connection("gsheets", type=GSheetsConnection)

# --- 2. 구글 시트 데이터 불러오기 ---
nurse_names = []
req_df = None

if sheet_url and "docs.google.com" in sheet_url:
    try:
        # 구글 시트의 '간호사명단' 워크시트 읽기
        req_df = conn.read(spreadsheet=sheet_url, worksheet="간호사명단", ttl="0")
        if "이름" in req_df.columns:
            nurse_names = req_df["이름"].dropna().astype(str).tolist()
            st.sidebar.success(f"구글 시트에서 총 {len(nurse_names)}명 명단 수신 완료!")
    except Exception as e:
        st.sidebar.warning("구글 시트 '간호사명단' 탭을 찾을 수 없거나 공유 설정(링크가 있는 모든 사용자에게 공개)이 필요합니다.")

# --- 3. 근무 규칙 및 날짜 설정 ---
st.sidebar.header("📅 연도 및 월 선택")
current_year = datetime.datetime.now().year
year = st.sidebar.number_input("연도", min_value=2024, max_value=2030, value=current_year)
month = st.sidebar.number_input("월", min_value=1, max_value=12, value=10)

_, num_days = calendar.monthrange(year, month)
weekdays_kr = ["월", "화", "수", "목", "금", "토", "일"]
date_headers = [f"{d}일({weekdays_kr[datetime.date(year, month, d).weekday()]})" for d in range(1, num_days + 1)]

# --- 4. 근무표 생성 알고리즘 ---
def generate_schedule(nurse_names, num_days, d_req=9, e_req=9, n_req=9, max_work=5, req_df=None):
    num_nurses = len(nurse_names)
    if num_nurses == 0:
        return None

    model = cp_model.CpModel()
    SHIFTS = [0, 1, 2, 3] # 0: OFF, 1: D, 2: E, 3: N
    SHIFT_NAMES = {0: '/', 1: 'D', 2: 'E', 3: 'N'}

    shifts = {}
    for n in range(num_nurses):
        for d in range(num_days):
            for s in SHIFTS:
                shifts[(n, d, s)] = model.NewBoolVar(f'shift_n{n}_d{d}_s{s}')

    # 제약조건: 하루 1개 근무
    for n in range(num_nurses):
        for d in range(num_days):
            model.AddExactlyOne(shifts[(n, d, s)] for s in SHIFTS)

    # 제약조건: 일별 필요인원 (D:9, E:9, N:9)
    for d in range(num_days):
        model.Add(sum(shifts[(n, d, 1)] for n in range(num_nurses)) == d_req)
        model.Add(sum(shifts[(n, d, 2)] for n in range(num_nurses)) == e_req)
        model.Add(sum(shifts[(n, d, 3)] for n in range(num_nurses)) == n_req)

    # 구글시트 신청 OFF 고정 반영
    if req_df is not None:
        off_keywords = ["/", "OFF", "오프", "연차"]
        for n_idx in range(min(num_nurses, len(req_df))):
            for d in range(num_days):
                col_name = f"{d+1}일"
                if col_name in req_df.columns:
                    val = str(req_df.iloc[n_idx][col_name]).strip().upper()
                    if val in [k.upper() for k in off_keywords]:
                        model.Add(shifts[(n_idx, d, 0)] == 1)

    # 역교대 금지 (N->D, N->E, E->D) 및 N 후 OFF
    for n in range(num_nurses):
        for d in range(num_days - 1):
            model.AddImplication(shifts[(n, d, 3)], shifts[(n, d + 1, 1)].Not())
            model.AddImplication(shifts[(n, d, 3)], shifts[(n, d + 1, 2)].Not())
            model.AddImplication(shifts[(n, d, 2)], shifts[(n, d + 1, 1)].Not())
            model.AddImplication(shifts[(n, d, 3)], shifts[(n, d + 1, 0)])

    # 연속근무 제한 (5일)
    for n in range(num_nurses):
        for d in range(num_days - max_work):
            model.Add(sum(shifts[(n, d + i, 0)] for i in range(max_work + 1)) >= 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30.0
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        schedule_data = []
        for n in range(num_nurses):
            row = [nurse_names[n]]
            for d in range(num_days):
                for s in SHIFTS:
                    if solver.Value(shifts[(n, d, s)]):
                        row.append(SHIFT_NAMES[s])
            schedule_data.append(row)
        return pd.DataFrame(schedule_data, columns=["이름"] + date_headers)
    return None

# --- 5. 화면 출력 및 구글 시트 저장 ---
if st.button("🚀 구글 시트 기반 근무표 생성하기", use_container_width=True):
    if not nurse_names:
        st.error("구글 시트 URL을 확인해 주세요.")
    else:
        with st.spinner("구글 시트 데이터를 바탕으로 근무표를 계산 중입니다..."):
            df_result = generate_schedule(nurse_names, num_days, req_df=req_df)
            
            if df_result is not None:
                st.success("🎉 근무표 생성이 완료되었습니다!")
                st.dataframe(df_result, use_container_width=True)

                # 구글 시트에 결과 쓰기 (새 워크시트 생성/업데이트)
                if st.button("📤 생성된 근무표를 구글 시트에 바로 저장하기"):
                    try:
                        conn.update(spreadsheet=sheet_url, worksheet=f"{year}년{month}월_근무표", data=df_result)
                        st.balloons()
                        st.success("구글 시트에 성공적으로 저장되었습니다!")
                    except Exception as e:
                        st.error(f"구글 시트 쓰기 권한이 필요합니다: {e}")