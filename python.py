import streamlit as st
import pandas as pd
from google import genai
from google.genai.errors import APIError
import io # Dùng để xử lý file Excel trong bộ nhớ

# --- Cấu hình Trang Streamlit ---
# Đặt tiêu đề và cấu hình layout rộng
st.set_page_config(
    page_title="App Phân Tích Báo Cáo Tài Chính",
    layout="wide",
    initial_sidebar_state="auto"
)

st.title("Ứng dụng Phân Tích Báo Cáo Tài Chính 📊")
st.markdown("Chào mừng bạn! Ứng dụng này giúp bạn tự động tính toán Tăng trưởng, Tỷ trọng, Chỉ số Tài chính và nhận phân tích chuyên sâu từ AI.")

# ************************* CẤU HÌNH GEMINI API & KHỞI TẠO CLIENT *************************

API_KEY = st.secrets.get("GEMINI_API_KEY")

# Khởi tạo Client và Chat Session nếu có API Key
if API_KEY:
    try:
        # Khởi tạo Client chung
        client = genai.Client(api_key=API_KEY)
        
        # 1. Khởi tạo Lịch sử Chat cho Chatbot
        if "messages" not in st.session_state:
            st.session_state["messages"] = [
                {"role": "assistant", "content": "Tôi là Gemini, chuyên gia phân tích tài chính. Hãy hỏi tôi bất kỳ câu hỏi nào về báo cáo tài chính của bạn."}
            ]

        # 2. Khởi tạo Chat Session (để giữ ngữ cảnh hội thoại)
        if "chat_session" not in st.session_state:
            # Sử dụng gemini-2.5-flash cho tốc độ và hiệu quả
            st.session_state["chat_session"] = client.chats.create(model="gemini-2.5-flash")

    except Exception as e:
        st.error(f"Lỗi khởi tạo Gemini Client: {e}. Vui lòng kiểm tra lại API Key.")
        client = None
else:
    st.warning("⚠️ Vui lòng cấu hình Khóa API 'GEMINI_API_KEY' trong Streamlit Secrets để sử dụng chức năng AI.")
    client = None

# *********************** KẾT THÚC CẤU HÌNH ***********************

# --- Hàm tính toán chính (Sử dụng Caching để Tối ưu hiệu suất) ---
@st.cache_data
def process_financial_data(df):
    """Thực hiện các phép tính Tăng trưởng và Tỷ trọng trên dữ liệu tài chính."""
    
    # Đảm bảo các cột số là kiểu số, thay thế lỗi bằng 0
    numeric_cols = ['Năm trước', 'Năm sau']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    
    # 1. Tính Tốc độ Tăng trưởng
    # Dùng .replace(0, 1e-9) cho Series Pandas để tránh lỗi chia cho 0
    df['Tốc độ tăng trưởng (%)'] = (
        (df['Năm sau'] - df['Năm trước']) / df['Năm trước'].replace(0, 1e-9)
    ) * 100

    # 2. Tính Tỷ trọng theo Tổng Tài sản (Tìm dòng Tổng Tài sản)
    # Cần đảm bảo tên "TỔNG TÀI SẢN" có trong cột 'Chỉ tiêu'
    tong_tai_san_row = df[df['Chỉ tiêu'].str.contains('TỔNG.*TÀI SẢN', case=False, na=False, regex=True)]
    
    if tong_tai_san_row.empty:
# Giả định nếu không tìm thấy "Tổng Tài Sản", sẽ dùng tổng cột làm mẫu số (ít chính xác hơn)
        # Hoặc báo lỗi để người dùng kiểm tra file. Ở đây chọn báo lỗi rõ ràng.
        raise ValueError("Không tìm thấy chỉ tiêu 'TỔNG TÀI SẢN' trong file để tính Tỷ trọng.")

    # Lấy giá trị Tổng Tài sản (chỉ lấy dòng đầu tiên nếu có nhiều hơn 1)
    tong_tai_san_N_1 = tong_tai_san_row['Năm trước'].iloc[0]
    tong_tai_san_N = tong_tai_san_row['Năm sau'].iloc[0]

    # Xử lý chia cho 0 thủ công cho giá trị đơn lẻ
    divisor_N_1 = tong_tai_san_N_1 if tong_tai_san_N_1 != 0 else 1e-9
    divisor_N = tong_tai_san_N if tong_tai_san_N != 0 else 1e-9

    # Tính tỷ trọng
    df['Tỷ trọng Năm trước (%)'] = (df['Năm trước'] / divisor_N_1) * 100
    df['Tỷ trọng Năm sau (%)'] = (df['Năm sau'] / divisor_N) * 100
    
    return df

# --- Hàm gọi API Gemini cho Phân tích chuyên sâu ---
def get_ai_analysis(data_for_ai, api_key):
    """Gửi dữ liệu phân tích đến Gemini API và nhận nhận xét chuyên sâu."""
    try:
        # Client đã được tạo ở đầu file, nhưng tạo lại ở đây nếu cần gọi độc lập
        # Sử dụng client đã tạo ở global scope (nếu có)
        client = genai.Client(api_key=api_key)
        model_name = 'gemini-2.5-flash' 

        prompt = f"""
        Bạn là một chuyên gia phân tích tài chính doanh nghiệp với nhiều năm kinh nghiệm thẩm định năng lực khách hàng.
        Dựa trên bảng dữ liệu chi tiết sau, hãy thực hiện phân tích:
        1. **Phân tích Tăng trưởng**: Nhận xét về tốc độ tăng trưởng của Doanh thu, Lợi nhuận, và Tổng tài sản.
        2. **Phân tích Cơ cấu**: Nhận xét về sự thay đổi tỷ trọng giữa Tài sản ngắn hạn/dài hạn, Nợ/Vốn chủ sở hữu.
        3. **Phân tích Thanh khoản**: Đánh giá Chỉ số Thanh toán Hiện hành.
        4. **Kết luận**: Tóm tắt ngắn gọn (3-4 đoạn văn) về Tình hình Tài chính và Năng lực Thẩm định của doanh nghiệp.

        Dữ liệu chi tiết đã được tính toán:
        {data_for_ai}
        """

        response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )
        return response.text

    except APIError as e:
        return f"Lỗi gọi Gemini API: Vui lòng kiểm tra Khóa API hoặc giới hạn sử dụng. Chi tiết lỗi: {e}"
    except Exception as e:
        return f"Đã xảy ra lỗi không xác định: {e}"


# --- PHẦN CHÍNH CỦA ỨNG DỤNG (MAIN APP FLOW) ---

# Chức năng 1: Tải File và Hiển thị Dữ liệu Thô
uploaded_file = st.file_uploader(
    "1. Tải file Excel Báo cáo Tài chính (Yêu cầu 3 cột: Chỉ tiêu | Năm trước | Năm sau)",
    type=['xlsx', 'xls']
)

if uploaded_file is not None:
    try:
        # Đọc file Excel
        df_raw = pd.read_excel(uploaded_file, header=None)
# Đảm bảo dữ liệu có đủ 3 cột cần thiết
        if df_raw.shape[1] < 3:
            st.error("Lỗi: File Excel phải có ít nhất 3 cột (Chỉ tiêu, Năm trước, Năm sau).")
        
        # Lấy 3 cột đầu tiên và đặt tên theo yêu cầu
        df_raw = df_raw.iloc[:, :3]
        df_raw.columns = ['Chỉ tiêu', 'Năm trước', 'Năm sau']
        
        # Loại bỏ các dòng rỗng hoàn toàn hoặc các dòng không có Chỉ tiêu
        df_raw = df_raw.dropna(subset=['Chỉ tiêu']).fillna(0)
        
        st.subheader("Bảng 1: Dữ liệu Báo cáo Tài chính đã tải lên")
        st.dataframe(df_raw, use_container_width=True)

        # Xử lý dữ liệu và tính toán
        df_processed = process_financial_data(df_raw.copy())

        if df_processed is not None:
            st.markdown("---")
            # --- Chức năng 2 & 3: Hiển thị Kết quả Tăng trưởng và Tỷ trọng ---
            st.subheader("Bảng 2: Phân tích Tăng trưởng và Cơ cấu Tài sản/Nguồn vốn")
            st.dataframe(df_processed.style.format({
                'Năm trước': '{:,.0f}',
                'Năm sau': '{:,.0f}',
                'Tốc độ tăng trưởng (%)': '{:+.2f}%', # Hiển thị dấu '+' cho tăng trưởng dương
                'Tỷ trọng Năm trước (%)': '{:.2f}%',
                'Tỷ trọng Năm sau (%)': '{:.2f}%'
            }), use_container_width=True)
            
            # --- Chức năng 4: Tính Chỉ số Thanh toán Hiện hành ---
            st.markdown("---")
            st.subheader("Bảng 3: Chỉ số Thanh toán Cơ bản")
            
            thanh_toan_hien_hanh_N = "N/A"
            thanh_toan_hien_hanh_N_1 = "N/A"
            delta_thanh_toan = None

            try:
                # Lấy Tài sản ngắn hạn (TSNH) và Nợ ngắn hạn (NNH)
                tsnh_n = df_processed[df_processed['Chỉ tiêu'].str.contains('TÀI SẢN NGẮN HẠN', case=False, na=False)]['Năm sau'].iloc[0]
                tsnh_n_1 = df_processed[df_processed['Chỉ tiêu'].str.contains('TÀI SẢN NGẮN HẠN', case=False, na=False)]['Năm trước'].iloc[0]

                no_ngan_han_N = df_processed[df_processed['Chỉ tiêu'].str.contains('NỢ NGẮN HẠN', case=False, na=False)]['Năm sau'].iloc[0]  
                no_ngan_han_N_1 = df_processed[df_processed['Chỉ tiêu'].str.contains('NỢ NGẮN HẠN', case=False, na=False)]['Năm trước'].iloc[0]

                # Tính toán Chỉ số Thanh toán Hiện hành (Current Ratio)
                thanh_toan_hien_hanh_N = tsnh_n / no_ngan_han_N if no_ngan_han_N != 0 else float('inf')
                thanh_toan_hien_hanh_N_1 = tsnh_n_1 / no_ngan_han_N_1 if no_ngan_han_N_1 != 0 else float('inf')
                
                # Tính Delta
                if thanh_toan_hien_hanh_N != float('inf') and thanh_toan_hien_hanh_N_1 != float('inf'):
delta_thanh_toan = thanh_toan_hien_hanh_N - thanh_toan_hien_hanh_N_1
                
                
                col1, col2 = st.columns(2)
                with col1:
                    st.metric(
                        label="Chỉ số Thanh toán Hiện hành (Năm trước - Tỷ suất)",
                        value=f"{thanh_toan_hien_hanh_N_1:.2f} lần" if thanh_toan_hien_hanh_N_1 != float('inf') else "Không xác định (Nợ = 0)"
                    )
                with col2:
                    st.metric(
                        label="Chỉ số Thanh toán Hiện hành (Năm sau - Tỷ suất)",
                        value=f"{thanh_toan_hien_hanh_N:.2f} lần" if thanh_toan_hien_hanh_N != float('inf') else "Không xác định (Nợ = 0)",
                        delta=f"{delta_thanh_toan:+.2f}" if delta_thanh_toan is not None else None
                    )
                    
            except IndexError:
                 st.warning("Thiếu chỉ tiêu **'TÀI SẢN NGẮN HẠN'** hoặc **'NỢ NGẮN HẠN'** để tính chỉ số Thanh toán Hiện hành.")
                 thanh_toan_hien_hanh_N = "N/A" # Gán lại để chuẩn bị dữ liệu AI
                 thanh_toan_hien_hanh_N_1 = "N/A"
            except ZeroDivisionError:
                 st.error("Lỗi chia cho 0 khi tính Chỉ số Thanh toán Hiện hành (Có thể do Nợ ngắn hạn = 0).")
                 thanh_toan_hien_hanh_N = "N/A"
                 thanh_toan_hien_hanh_N_1 = "N/A"
            
            # --- Chức năng 5: Nhận xét AI ---
            st.markdown("---")
            st.subheader("4. Nhận xét Chuyên sâu từ AI (Gemini)")
            
            # Chuẩn bị dữ liệu để gửi cho AI (kết hợp cả bảng phân tích và chỉ số chính)
            data_for_ai = pd.DataFrame({
                'Chỉ tiêu': [
                    'Toàn bộ Bảng phân tích Tăng trưởng và Cơ cấu',
                    'Thanh toán hiện hành (N-1)',  
                    'Thanh toán hiện hành (N)'
                ],
                'Giá trị': [
                    df_processed.to_markdown(index=False),
                    f"{thanh_toan_hien_hanh_N_1:.2f}" if isinstance(thanh_toan_hien_hanh_N_1, float) else "N/A",  
                    f"{thanh_toan_hien_hanh_N:.2f}" if isinstance(thanh_toan_hien_hanh_N, float) else "N/A"
                ]
            }).to_markdown(index=False) 

            if st.button("▶️ Bắt đầu Phân tích AI", type="primary"):
                api_key_check = st.secrets.get("GEMINI_API_KEY") 
                
                if api_key_check:
                    with st.spinner('Đang gửi dữ liệu và chờ Gemini phân tích (quá trình này có thể mất vài giây)...'):
                        ai_result = get_ai_analysis(data_for_ai, api_key_check)
st.markdown("**Kết quả Phân tích từ Chuyên gia Gemini:**")
                        st.info(ai_result)
                else:
                    st.error("Lỗi: Không tìm thấy Khóa API. Vui lòng cấu hình Khóa 'GEMINI_API_KEY' trong Streamlit Secrets.")

    except ValueError as ve:
        st.error(f"Lỗi cấu trúc dữ liệu: {ve}")
        st.warning("Vui lòng đảm bảo file Excel của bạn có các cột: **Chỉ tiêu** | **Năm trước** | **Năm sau** và bao gồm dòng **'TỔNG TÀI SẢN'.**")
    except Exception as e:
        st.error(f"Có lỗi xảy ra khi đọc hoặc xử lý file: {e}. Vui lòng kiểm tra định dạng file và dữ liệu.")

else:
    st.info("Vui lòng tải lên file Excel (3 cột: Chỉ tiêu, Năm trước, Năm sau) để bắt đầu phân tích.")
    st.markdown("""
    **Cấu trúc file mẫu cần có:**
    | Chỉ tiêu | Năm trước | Năm sau |
    |---|---|---|
    | Tiền mặt | 1000 | 1200 |
    | Tài sản ngắn hạn | 5000 | 6500 |
    | Nợ ngắn hạn | 2500 | 3000 |
    | Doanh thu | 15000 | 18000 |
    | **TỔNG CỘNG TÀI SẢN** | **10000** | **15000** |
    """)

# ************************* KHUNG CHAT HỎI ĐÁP *************************
st.markdown("---")
st.subheader("5. Chatbot Hỏi đáp Tài chính (Powered by Gemini)")

# Hiển thị lịch sử chat
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Xử lý input từ người dùng
if prompt := st.chat_input("Bạn muốn hỏi gì thêm về báo cáo tài chính hoặc các chỉ số?"):
    
    # Kiểm tra trạng thái client
    if client is None or "chat_session" not in st.session_state:
        st.error("Chatbot không hoạt động do lỗi cấu hình API Key. Vui lòng kiểm tra Streamlit Secrets.")
    else:
        # Thêm tin nhắn người dùng vào lịch sử
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Gửi tin nhắn đến Gemini
        with st.chat_message("assistant"):
            with st.spinner("Đang suy nghĩ..."):
                try:
                    # Gửi tin nhắn qua Chat Session để giữ ngữ cảnh
                    response = st.session_state.chat_session.send_message(prompt)
                    st.markdown(response.text)
                    
                    # Thêm phản hồi của AI vào lịch sử
                    st.session_state.messages.append({"role": "assistant", "content": response.text})
                
                except Exception as e:
                    error_message = f"Lỗi Gemini Chat: {e}. Vui lòng thử lại."
                    st.error(error_message)
                    st.session_state.messages.append({"role": "assistant", "content": error_message})
