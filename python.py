# python.py
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from google import genai
from google.genai.errors import APIError

# --------- NEW: đọc file Word ----------
try:
    from docx import Document
except Exception as e:
    Document = None

# ================== CẤU HÌNH TRANG ==================
st.set_page_config(page_title="Đánh giá Phương án Kinh doanh (Word) – Streamlit", layout="wide")
st.title("📄 Đánh giá Phương án Kinh doanh từ file Word")

st.caption(
    "Tải phương án (.docx) → Bấm **Lọc dữ liệu bằng AI** → Tự động tạo *bảng dòng tiền* và *các chỉ số hiệu quả*: "
    "**NPV, IRR, PP, DPP**. Bạn có thể yêu cầu AI phân tích kết quả ngay trong app."
)

# ================== TIỆN ÍCH CHUNG ==================
def fmt_percent(x):
    try:
        return f"{float(x)*100:.2f}%"
    except:
        return "—"

def to_float_safe(x, default=None):
    try:
        if x is None: return default
        if isinstance(x, str):
            x = x.replace(",", "").replace("%", "").strip()
        return float(x)
    except:
        return default

def irr_bisection(cashflows, low=-0.999999, high=10.0, tol=1e-6, max_iter=200):
    """
    IRR tìm nghiệm NPV=0 bằng nhị phân.
    Trả về None nếu không hội tụ hoặc không có đổi dấu.
    """
    def npv(rate):
        return sum(cf / ((1 + rate) ** t) for t, cf in enumerate(cashflows))
    try:
        f_low = npv(low)
        f_high = npv(high)
        # Nếu không đổi dấu, mở rộng tìm biên độ
        expand = 0
        while f_low * f_high > 0 and expand < 6:
            high *= 2
            f_high = npv(high)
            expand += 1
        if f_low * f_high > 0:
            return None
        for _ in range(max_iter):
            mid = (low + high) / 2
            f_mid = npv(mid)
            if abs(f_mid) < tol:
                return mid
            if f_low * f_mid < 0:
                high, f_high = mid, f_mid
            else:
                low, f_low = mid, f_mid
        return (low + high) / 2
    except Exception:
        return None

def payback_period(cashflows):
    """
    PP: thời điểm tích lũy dòng tiền (không chiết khấu) >= 0.
    Nội suy tuyến tính trong năm đạt điểm hoàn vốn.
    Trả về số năm (float) hoặc None.
    """
    cum = 0.0
    for t in range(len(cashflows)):
        prev = cum
        cum += cashflows[t]
        if cum >= 0:
            if t == 0:  # hoàn vốn ngay tại t0
                return 0.0
            # nội suy trong năm t
            needed = -prev
            if cashflows[t] == 0:
                return float(t)
            frac = needed / cashflows[t]
            return (t - 1) + (1 - frac)
    return None

def discounted_payback_period(cashflows, rate):
    """
    DPP: thời điểm tích lũy dòng tiền chiết khấu >= 0.
    Nội suy tuyến tính trong năm đạt điểm hoàn vốn chiết khấu.
    """
    cum = 0.0
    for t in range(len(cashflows)):
        prev = cum
        cf_disc = cashflows[t] / ((1 + rate) ** t)
        cum += cf_disc
        if cum >= 0:
            if t == 0:
                return 0.0
            needed = -prev
            if cf_disc == 0:
                return float(t)
            frac = needed / cf_disc
            return (t - 1) + (1 - frac)
    return None

def straight_line_depreciation(capex, life_years):
    if life_years and life_years > 0:
        return capex / life_years
    return 0.0

def build_cashflow_table(
    investment_capex,
    project_life_years,
    annual_revenues,
    annual_costs,
    wacc,
    tax_rate,
    revenue_growth=None,
    cost_growth=None,
    depreciation_mode="straight_line"
):
    """
    Tạo bảng FCFF (Free Cash Flow to Firm).
    Quy ước:
      - CF năm 0: -Capex (dòng tiền ra đầu tư)
      - Khấu hao: mặc định đường thẳng
      - EBIT = Doanh thu - Chi phí - Khấu hao
      - Thuế = max(EBIT, 0) * thuế_suất
      - FCFF = EBIT*(1 - tax) + Khấu hao  (năm >=1), năm 0 = -Capex
    Cho phép annual_revenues/costs là scalar hoặc list độ dài = project_life_years.
    Nếu có tỷ lệ tăng trưởng, áp dụng tăng dần theo năm (CAGR đơn giản).
    """
    n = int(project_life_years or 0)
    if n <= 0:
        raise ValueError("Dòng đời dự án (năm) phải > 0.")

    # Chuẩn hóa revenue/cost list
    def expand_series(x, growth, n):
        # Nếu x là list/tuple và đủ độ dài thì dùng luôn
        if isinstance(x, (list, tuple, np.ndarray)) and len(x) >= n:
            return [to_float_safe(v, 0.0) for v in x[:n]]
        # Nếu x là 1 số → tạo chuỗi theo tăng trưởng
        base = to_float_safe(x, 0.0)
        g = to_float_safe(growth, 0.0)
        series = []
        for t in range(1, n + 1):
            # năm 1 = base, năm t = base * (1+g)^(t-1)
            series.append(base * ((1 + g) ** (t - 1)))
        return series

    rev = expand_series(annual_revenues, revenue_growth, n)
    cost = expand_series(annual_costs, cost_growth, n)

    dep = 0.0
    if depreciation_mode == "straight_line":
        dep = straight_line_depreciation(to_float_safe(investment_capex, 0.0), n)

    rows = []
    # Năm 0 (đầu tư)
    rows.append({
        "Năm": 0,
        "Doanh thu": 0.0,
        "Chi phí": 0.0,
        "Khấu hao": 0.0,
        "EBIT": 0.0,
        "Thuế": 0.0,
        "FCFF": -to_float_safe(investment_capex, 0.0)
    })

    for t in range(1, n + 1):
        r = rev[t - 1]
        c = cost[t - 1]
        ebit = r - c - dep
        tax = max(ebit, 0.0) * to_float_safe(tax_rate, 0.0)
        fcff = (ebit * (1 - to_float_safe(tax_rate, 0.0))) + dep
        rows.append({
            "Năm": t,
            "Doanh thu": r,
            "Chi phí": c,
            "Khấu hao": dep,
            "EBIT": ebit,
            "Thuế": tax,
            "FCFF": fcff
        })

    df = pd.DataFrame(rows)
    # NPV & IRR
    rate = to_float_safe(wacc, 0.0)
    cashflows = df["FCFF"].tolist()
    npv = sum(cf / ((1 + rate) ** t) for t, cf in enumerate(cashflows))
    irr = irr_bisection(cashflows)

    pp = payback_period(cashflows)
    dpp = discounted_payback_period(cashflows, rate)

    return df, npv, irr, pp, dpp

# ================== GỌI GEMINI ==================
def get_gemini_client():
    api_key = st.secrets.get("GEMINI_API_KEY", None)
    if api_key is None:
        # Thử đọc từ biến môi trường
        import os
        api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        st.error("Không tìm thấy khóa `GEMINI_API_KEY` trong Secrets hoặc biến môi trường.")
        return None
    try:
        client = genai.Client(api_key=api_key)
        return client
    except Exception as e:
        st.error(f"Lỗi khởi tạo Gemini Client: {e}")
        return None

EXTRACTION_SYSTEM_PROMPT = """
Bạn là một trợ lý tạo cấu trúc dữ liệu cho phương án kinh doanh. 
Hãy phân tích văn bản và TRẢ VỀ JSON hợp lệ, KHÔNG kèm giải thích.
Trường bắt buộc:
- investment_capex: số tiền đầu tư ban đầu (VND)
- project_life_years: số năm hoạt động (int)
- revenues: có thể là số (doanh thu năm 1) hoặc mảng số (theo từng năm)
- costs: có thể là số (chi phí năm 1) hoặc mảng số (theo từng năm)
- wacc_percent: % WACC (0-100)
- tax_rate_percent: % thuế TNDN (0-100)
Trường tùy chọn:
- revenue_growth_percent: % tăng trưởng doanh thu mỗi năm (nếu revenues là 1 số)
- cost_growth_percent: % tăng trưởng chi phí mỗi năm (nếu costs là 1 số)
- notes: chuỗi ngắn ghi chú

Ví dụ JSON:
{
 "investment_capex": 12000000000,
 "project_life_years": 7,
 "revenues": [3500000000, 3605000000, 3713150000, 3824544500, 394,  ...],
 "costs": 2000000000,
 "wacc_percent": 12.0,
 "tax_rate_percent": 20.0,
 "revenue_growth_percent": 3.0,
 "cost_growth_percent": 2.0,
 "notes": "Doanh thu tăng trưởng ổn định"
}
"""

def ai_extract_from_text(full_text: str):
    client = get_gemini_client()
    if not client:
        return None, "Gemini client chưa sẵn sàng."
    try:
        prompt = f"""{EXTRACTION_SYSTEM_PROMPT}

--- VĂN BẢN PHÂN TÍCH ---
{full_text}
"""
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        raw = resp.text.strip()
        # Thử tìm JSON trong văn bản trả về
        import json, re
        # Nếu model trả JSON thuần: parse luôn
        try:
            data = json.loads(raw)
        except:
            # Tìm khối { ... }
            m = re.search(r'\{.*\}', raw, flags=re.S)
            if not m:
                return None, f"Không tìm thấy JSON trong phản hồi AI:\n{raw[:4000]}"
            data = json.loads(m.group(0))
        return data, None
    except APIError as e:
        return None, f"Lỗi gọi Gemini API: {e}"
    except Exception as e:
        return None, f"Lỗi không xác định khi trích xuất: {e}"

def ai_analyze_metrics(metrics_payload_markdown: str):
    client = get_gemini_client()
    if not client:
        return "Gemini client chưa sẵn sàng."
    try:
        prompt = f"""
Bạn là chuyên gia thẩm định dự án. Dựa trên các chỉ số và bảng dòng tiền ở dưới, 
hãy phân tích ngắn gọn (3–5 đoạn, mỗi đoạn 2–3 câu), tập trung vào:
- mức tạo giá trị (NPV), độ nhạy theo WACC
- khả năng hoàn vốn (PP, DPP) so với vòng đời dự án
- chất lượng lợi nhuận (IRR so với WACC), rủi ro và khuyến nghị

DỮ LIỆU:
{metrics_payload_markdown}
"""
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return resp.text
    except Exception as e:
        return f"Không thể phân tích bằng AI: {e}"

# ================== GIAO DIỆN: TẢI WORD & TRÍCH XUẤT ==================
colL, colR = st.columns([2, 1])
with colL:
    uploaded = st.file_uploader("1) Tải file Word (.docx) phương án kinh doanh", type=["docx"])
    if Document is None:
        st.warning("Thiếu thư viện `python-docx`. Vui lòng cài: `pip install python-docx`")
with colR:
    st.info("Mẹo: Văn bản nên có các khu vực đề cập rõ **vốn đầu tư**, **doanh thu**, **chi phí**, **WACC**, **thuế**, **thời gian dự án**.")

text_content = ""
if uploaded is not None and Document is not None:
    try:
        doc = Document(BytesIO(uploaded.read()))
        text_content = "\n".join([p.text for p in doc.paragraphs if p.text.strip() != ""])
        with st.expander("📜 Xem nhanh nội dung đã tải"):
            st.write(text_content[:4000] + ("..." if len(text_content) > 4000 else ""))
    except Exception as e:
        st.error(f"Không đọc được file Word: {e}")

st.markdown("---")
st.subheader("2) Lọc dữ liệu đầu vào bằng AI")

extract_btn = st.button("🧠 Lọc dữ liệu bằng AI", type="primary", disabled=(not text_content))

default_payload = {}
if extract_btn and text_content:
    with st.spinner("Đang trích xuất thông tin từ văn bản bằng AI..."):
        data, err = ai_extract_from_text(text_content)
        if err:
            st.error(err)
        else:
            default_payload = data or {}

# Cho phép hiệu chỉnh tay (khi AI thiếu/nhầm)
with st.form("manual_inputs"):
    st.caption("Bạn có thể chỉnh sửa các giá trị AI trích xuất (nếu cần).")
    c1, c2, c3 = st.columns(3)
    investment_capex = c1.number_input("Vốn đầu tư (VND)", min_value=0.0, value=to_float_safe(default_payload.get("investment_capex"), 0.0), step=1e6, format="%.0f")
    project_life_years = int(c2.number_input("Dòng đời dự án (năm)", min_value=1, value=int(to_float_safe(default_payload.get("project_life_years"), 5))))
    wacc_percent = c3.number_input("WACC (%)", min_value=0.0, max_value=100.0, value=to_float_safe(default_payload.get("wacc_percent"), 12.0))
    tax_percent = c1.number_input("Thuế TNDN (%)", min_value=0.0, max_value=100.0, value=to_float_safe(default_payload.get("tax_rate_percent"), 20.0))
    revenue_growth = c2.number_input("Tăng trưởng doanh thu (%/năm)", min_value=-100.0, max_value=100.0, value=to_float_safe(default_payload.get("revenue_growth_percent"), 0.0))
    cost_growth = c3.number_input("Tăng trưởng chi phí (%/năm)", min_value=-100.0, max_value=100.0, value=to_float_safe(default_payload.get("cost_growth_percent"), 0.0))

    st.markdown("**Doanh thu & Chi phí** (chọn *một* trong hai cách nhập mỗi mục):")
    d1, d2 = st.columns(2)
    rev_scalar = d1.number_input("Doanh thu năm 1 (VND) – nếu cố định/gốc", min_value=0.0, value=to_float_safe(default_payload.get("revenues"), 0.0) if isinstance(default_payload.get("revenues"), (int, float, str)) else 0.0, step=1e6, format="%.0f")
    cost_scalar = d2.number_input("Chi phí năm 1 (VND) – nếu cố định/gốc", min_value=0.0, value=to_float_safe(default_payload.get("costs"), 0.0) if isinstance(default_payload.get("costs"), (int, float, str)) else 0.0, step=1e6, format="%.0f")

    rev_list_str = d1.text_area("Hoặc danh sách Doanh thu theo năm (phân tách bằng dấu phẩy)", value=",".join([str(x) for x in default_payload.get("revenues", [])]) if isinstance(default_payload.get("revenues", None), list) else "")
    cost_list_str = d2.text_area("Hoặc danh sách Chi phí theo năm (phân tách bằng dấu phẩy)", value=",".join([str(x) for x in default_payload.get("costs", [])]) if isinstance(default_payload.get("costs", None), list) else "")

    submitted = st.form_submit_button("🚀 Tạo bảng dòng tiền & tính chỉ số", type="primary")

# Chuẩn hóa revenues/costs từ form
def parse_list_field(s):
    if not s or not s.strip():
        return None
    try:
        vals = [to_float_safe(v.strip(), 0.0) for v in s.split(",") if v.strip() != ""]
        return vals if len(vals) > 0 else None
    except:
        return None

if submitted:
    revenues_input = parse_list_field(rev_list_str) if parse_list_field(rev_list_str) is not None else rev_scalar
    costs_input = parse_list_field(cost_list_str) if parse_list_field(cost_list_str) is not None else cost_scalar

    try:
        df_cf, npv, irr, pp, dpp = build_cashflow_table(
            investment_capex=investment_capex,
            project_life_years=project_life_years,
            annual_revenues=revenues_input,
            annual_costs=costs_input,
            wacc=wacc_percent/100.0,
            tax_rate=tax_percent/100.0,
            revenue_growth=revenue_growth/100.0,
            cost_growth=cost_growth/100.0,
            depreciation_mode="straight_line"
        )

        st.subheader("3) Bảng dòng tiền dự án (FCFF)")
        st.dataframe(
            df_cf.style.format({
                "Doanh thu": "{:,.0f}",
                "Chi phí": "{:,.0f}",
                "Khấu hao": "{:,.0f}",
                "EBIT": "{:,.0f}",
                "Thuế": "{:,.0f}",
                "FCFF": "{:,.0f}",
            }),
            use_container_width=True,
            height=420
        )

        st.subheader("4) Chỉ số hiệu quả dự án")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("NPV (VND)", f"{npv:,.0f}")
        m2.metric("IRR", f"{irr*100:.2f}%" if irr is not None else "—")
        m3.metric("PP (năm)", f"{pp:.2f}" if pp is not None else "—")
        m4.metric("DPP (năm)", f"{dpp:.2f}" if dpp is not None else "—")

        with st.expander("📥 Tải về bảng dòng tiền (CSV)"):
            csv = df_cf.to_csv(index=False).encode("utf-8-sig")
            st.download_button("Tải CSV", data=csv, file_name="cashflow.csv", mime="text/csv")

        # ============== PHÂN TÍCH AI ==============
        st.subheader("5) Phân tích bởi AI")
        payload_md = pd.DataFrame({
            "Thông tin": ["Vốn đầu tư", "Dòng đời (năm)", "WACC", "Thuế", "NPV", "IRR", "PP", "DPP"],
            "Giá trị": [
                f"{investment_capex:,.0f}",
                f"{project_life_years}",
                f"{wacc_percent:.2f}%",
                f"{tax_percent:.2f}%",
                f"{npv:,.0f}",
                f"{irr*100:.2f}%" if irr is not None else "—",
                f"{pp:.2f}" if pp is not None else "—",
                f"{dpp:.2f}" if dpp is not None else "—",
            ]
        })
        st.dataframe(payload_md, use_container_width=True, height=260)

        if st.button("🤖 Yêu cầu AI phân tích các chỉ số"):
            with st.spinner("AI đang phân tích..."):
                # ghép thêm bảng dòng tiền ở dạng markdown
                md_cf = df_cf.to_markdown(index=False)
                md_all = payload_md.to_markdown(index=False) + "\n\n" + md_cf
                ai_text = ai_analyze_metrics(md_all)
                st.success("Kết quả phân tích từ AI:")
                st.write(ai_text)

    except ValueError as ve:
        st.error(f"Lỗi dữ liệu đầu vào: {ve}")
    except Exception as e:
        st.error(f"Không thể tạo bảng dòng tiền / tính chỉ số: {e}")
