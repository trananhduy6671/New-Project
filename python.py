import streamlit as st
import pandas as pd
import numpy as np
import numpy_financial as npf
import docx2txt
import google.generativeai as genai
import json
import re

# --- Cấu hình Trang Streamlit ---
st.set_page_config(
    page_title="App Đánh giá Phương án Kinh doanh",
    page_icon="💼",
    layout="wide"
)

# --- Giao diện chính ---
st.title("💼 App Đánh giá Phương án Kinh doanh bằng AI")
st.markdown("**Xây dựng bởi một chuyên gia Python & Streamlit**")
st.info(
    "**Hướng dẫn:**\n"
    "1. Nhập `API Key` của bạn từ Google AI Studio.\n"
    "2. Tải lên file Word (`.docx`) chứa phương án kinh doanh của bạn.\n"
    "3. Nhấn nút `Lọc dữ liệu từ file Word` để AI trích xuất các thông số chính.\n"
    "4. Xem bảng dòng tiền và các chỉ số tài chính được tự động tính toán.\n"
    "5. Nhấn nút `Yêu cầu AI phân tích các chỉ số` để nhận đánh giá chuyên sâu về dự án."
)


# --- Hàm xử lý chính ---

def parse_json_from_text(text):
    """Trích xuất chuỗi JSON từ văn bản trả về của AI."""
    match = re.search(r"```json\n({.*?})\n```", text, re.DOTALL)
    if match:
        json_str = match.group(1)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            st.error(f"Lỗi khi phân tích JSON: {e}")
            st.code(json_str) # Hiển thị chuỗi JSON bị lỗi để debug
            return None
    st.error("Không tìm thấy định dạng JSON hợp lệ trong phản hồi của AI.")
    st.text(text)
    return None

def extract_info_with_ai(text, api_key):
    """Sử dụng AI để lọc thông tin từ văn bản."""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
        Bạn là một chuyên gia phân tích tài chính. Hãy đọc kỹ văn bản phương án kinh doanh sau đây và trích xuất các thông tin sau:
        1. Vốn đầu tư (investment_capital): Tổng số tiền đầu tư ban đầu.
        2. Dòng đời dự án (project_lifetime): Số năm dự án hoạt động.
        3. Doanh thu hàng năm (annual_revenue): Doanh thu trung bình mỗi năm. Nếu doanh thu thay đổi, hãy tính trung bình.
        4. Chi phí hoạt động hàng năm (annual_cost): Chi phí trung bình mỗi năm, không bao gồm vốn đầu tư ban đầu.
        5. Chi phí sử dụng vốn bình quân (wacc): Tỷ lệ WACC, dưới dạng số thập phân (ví dụ: 15% là 0.15).
        6. Thuế suất thuế thu nhập doanh nghiệp (tax_rate): Tỷ lệ thuế, dưới dạng số thập phân (ví dụ: 20% là 0.2).

        Hãy trả về kết quả dưới dạng một đối tượng JSON nằm trong khối ```json ... ```. Chỉ trả về JSON, không giải thích gì thêm.

        Văn bản phương án kinh doanh:
        ---
        {text}
        ---
        """
        
        response = model.generate_content(prompt)
        # st.markdown(response.text) # Uncomment for debugging AI response
        return parse_json_from_text(response.text)

    except Exception as e:
        st.error(f"Đã có lỗi xảy ra khi gọi API của AI: {e}")
        return None

@st.cache_data
def build_cash_flow_table(project_data):
    """Xây dựng bảng dòng tiền chi tiết."""
    try:
        years = int(project_data['project_lifetime'])
        investment = float(project_data['investment_capital'])
        revenue = float(project_data['annual_revenue'])
        cost = float(project_data['annual_cost'])
        tax_rate = float(project_data['tax_rate'])
        
        # Giả định khấu hao đường thẳng
        depreciation = investment / years
        
        # Tạo DataFrame
        df = pd.DataFrame(index=[f"Năm {i}" for i in range(years + 1)])
        df['Doanh thu'] = [0] + [revenue] * years
        df['Chi phí hoạt động'] = [0] + [cost] * years
        df['Khấu hao'] = [0] + [depreciation] * years
        
        df['Lợi nhuận trước thuế (EBT)'] = df['Doanh thu'] - df['Chi phí hoạt động'] - df['Khấu hao']
        df['Thuế (TNDN)'] = df['Lợi nhuận trước thuế (EBT)'].apply(lambda ebt: ebt * tax_rate if ebt > 0 else 0)
        df['Lợi nhuận sau thuế (EAT)'] = df['Lợi nhuận trước thuế (EBT)'] - df['Thuế (TNDN)']
        
        # Dòng tiền thuần = Lợi nhuận sau thuế + Khấu hao (vì khấu hao không phải chi phí tiền mặt)
        df['Dòng tiền thuần (NCF)'] = df['Lợi nhuận sau thuế (EAT)'] + df['Khấu hao']
        df.loc['Năm 0', 'Dòng tiền thuần (NCF)'] = -investment
        
        return df
    except (KeyError, TypeError, ValueError) as e:
        st.error(f"Dữ liệu đầu vào không hợp lệ để xây dựng bảng dòng tiền. Lỗi: {e}")
        return None


@st.cache_data
def calculate_metrics(_df, wacc):
    """Tính toán các chỉ số tài chính quan trọng."""
    try:
        wacc = float(wacc)
        cash_flows = _df['Dòng tiền thuần (NCF)'].values
        
        # NPV
        npv = npf.npv(wacc, cash_flows)
        
        # IRR
        try:
            irr = npf.irr(cash_flows)
        except:
            irr = np.nan # Không tính được IRR

        # Payback Period (PP)
        cumulative_cash_flow = np.cumsum(cash_flows[1:]) # Bắt đầu từ năm 1
        payback_years = np.where(cumulative_cash_flow >= cash_flows[0] * -1)[0]
        if len(payback_years) > 0:
            year_before_payback = payback_years[0]
            unrecovered_amount = -cash_flows[0] - (cumulative_cash_flow[year_before_payback - 1] if year_before_payback > 0 else 0)
            cash_flow_in_payback_year = cash_flows[year_before_payback + 1]
            pp = year_before_payback + unrecovered_amount / cash_flow_in_payback_year
        else:
            pp = np.inf # Không hoàn vốn

        # Discounted Payback Period (DPP)
        discounted_flows = [cf / ((1 + wacc) ** i) for i, cf in enumerate(cash_flows)]
        cumulative_discounted_flow = np.cumsum(discounted_flows[1:])
        d_payback_years = np.where(cumulative_discounted_flow >= -discounted_flows[0])[0]
        if len(d_payback_years) > 0:
            year_before_d_payback = d_payback_years[0]
            unrecovered_d_amount = -discounted_flows[0] - (cumulative_discounted_flow[year_before_d_payback - 1] if year_before_d_payback > 0 else 0)
            dcf_in_payback_year = discounted_flows[year_before_d_payback + 1]
            dpp = year_before_d_payback + unrecovered_d_amount / dcf_in_payback_year
        else:
            dpp = np.inf
            
        return {'NPV': npv, 'IRR': irr, 'PP': pp, 'DPP': dpp}
    except Exception as e:
        st.error(f"Lỗi khi tính toán chỉ số: {e}")
        return None

def analyze_metrics_with_ai(metrics, project_data, api_key):
    """Yêu cầu AI phân tích các chỉ số đã tính toán."""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
        Với vai trò là một chuyên gia tư vấn đầu tư cấp cao, hãy phân tích các chỉ số hiệu quả của một dự án và đưa ra nhận định.
        
        Bối cảnh dự án:
        - Vốn đầu tư ban đầu: {project_data['investment_capital']:,.0f}
        - Vòng đời dự án: {project_data['project_lifetime']} năm
        - WACC (Tỷ suất chiết khấu): {project_data['wacc']:.2%}

        Các chỉ số hiệu quả đã tính toán:
        - NPV (Giá trị hiện tại ròng): {metrics['NPV']:,.0f}
        - IRR (Tỷ suất hoàn vốn nội bộ): {metrics['IRR']:.2%}
        - Thời gian hoàn vốn (PP): {'{:.2f} năm'.format(metrics['PP']) if metrics['PP'] != np.inf else 'Không hoàn vốn'}
        - Thời gian hoàn vốn có chiết khấu (DPP): {'{:.2f} năm'.format(metrics['DPP']) if metrics['DPP'] != np.inf else 'Không hoàn vốn'}

        Dựa trên những con số này, hãy đưa ra một bản phân tích chuyên sâu (khoảng 3-4 đoạn văn) về các khía cạnh sau:
        1. Tính khả thi của dự án: Dự án có đáng để đầu tư không? Tại sao? (Dựa vào NPV và IRR so với WACC).
        2. Mức độ rủi ro về thời gian: Thời gian hoàn vốn (PP và DPP) nói lên điều gì về rủi ro của dự án?
        3. Kết luận và đề xuất: Tóm tắt lại và đưa ra lời khuyên cho nhà đầu tư.
        """
        
        response = model.generate_content(prompt)
        return response.text
        
    except Exception as e:
        st.error(f"Đã có lỗi xảy ra khi gọi API của AI: {e}")
        return "Không thể thực hiện phân tích."

# --- Luồng hoạt động của App ---
api_key = st.text_input("Nhập Gemini API Key của bạn tại đây", type="password", help="Bạn có thể lấy API key miễn phí tại Google AI Studio.")

uploaded_file = st.file_uploader(
    "1. Tải lên file phương án kinh doanh (.docx)",
    type=['docx']
)

if uploaded_file:
    # Khởi tạo session state để lưu trữ dữ liệu
    if 'project_data' not in st.session_state:
        st.session_state.project_data = None
    if 'analysis_result' not in st.session_state:
        st.session_state.analysis_result = None

    try:
        document_text = docx2txt.process(uploaded_file)
        
        st.expander("Xem nội dung file Word đã tải lên").text(document_text[:2000] + "...") # Hiển thị 2000 ký tự đầu

        if st.button("Lọc dữ liệu từ file Word", type="primary"):
            if not api_key:
                st.warning("Vui lòng nhập API Key trước khi thực hiện lọc dữ liệu.")
            else:
                with st.spinner("AI đang phân tích file Word... Vui lòng chờ trong giây lát."):
                    extracted_data = extract_info_with_ai(document_text, api_key)
                    if extracted_data:
                        st.session_state.project_data = extracted_data
                        # Reset các kết quả cũ
                        st.session_state.analysis_result = None 
                        st.success("AI đã trích xuất thông tin thành công!")
                    else:
                        st.error("Không thể trích xuất thông tin từ file. Vui lòng kiểm tra lại nội dung file hoặc API key.")

        if st.session_state.project_data:
            st.divider()
            project_data = st.session_state.project_data
            
            st.subheader("1. Thông tin dự án do AI trích xuất")
            st.json(project_data)
            
            st.subheader("2. Bảng dòng tiền dự án")
            cf_df = build_cash_flow_table(project_data)
            if cf_df is not None:
                st.dataframe(cf_df.style.format("{:,.0f}"))
                
                st.subheader("3. Các chỉ số đánh giá hiệu quả dự án")
                metrics = calculate_metrics(cf_df, project_data['wacc'])
                if metrics:
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("NPV (Giá trị hiện tại ròng)", f"{metrics['NPV']:,.0f}")
                    col2.metric("IRR (Tỷ suất hoàn vốn nội bộ)", f"{metrics['IRR']:.2%}")
                    col3.metric("PP (Thời gian hoàn vốn)", f"{'{:.2f}'.format(metrics['PP']) if metrics['PP'] != np.inf else 'N/A'} năm")
                    col4.metric("DPP (Thời gian hoàn vốn có chiết khấu)", f"{'{:.2f}'.format(metrics['DPP']) if metrics['DPP'] != np.inf else 'N/A'} năm")

                    st.divider()
                    st.subheader("4. Yêu cầu AI phân tích các chỉ số")
                    if st.button("Bắt đầu phân tích", help="AI sẽ đưa ra nhận định dựa trên các chỉ số đã tính toán."):
                        if not api_key:
                            st.warning("Vui lòng nhập API Key để AI thực hiện phân tích.")
                        else:
                            with st.spinner("Chuyên gia AI đang phân tích..."):
                                analysis_text = analyze_metrics_with_ai(metrics, project_data, api_key)
                                st.session_state.analysis_result = analysis_text
                    
                    if st.session_state.analysis_result:
                        st.markdown("**Đánh giá từ Chuyên gia AI:**")
                        st.info(st.session_state.analysis_result)

    except Exception as e:
        st.error(f"Đã có lỗi xảy ra khi xử lý file: {e}")
