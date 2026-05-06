import streamlit as st
import pandas as pd
import json
import io
import urllib.request
import urllib.error
from datetime import datetime

st.set_page_config(page_title="商品音樂搜尋", page_icon="🎵", layout="wide")

ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin123")
USER_PASSWORD  = st.secrets.get("USER_PASSWORD",  "user123")
GEMINI_KEY     = st.secrets.get("GEMINI_API_KEY", "")

if "role"       not in st.session_state: st.session_state.role = None
if "music_df"   not in st.session_state: st.session_state.music_df = None
if "results"    not in st.session_state: st.session_state.results = []
if "last_query" not in st.session_state: st.session_state.last_query = {}

# ══════════════════════════════════════
#  登入
# ══════════════════════════════════════
def show_login():
    st.markdown("## 🎵 商品音樂搜尋系統")
    st.markdown("請輸入密碼登入")
    pw = st.text_input("密碼", type="password")
    if st.button("登入", use_container_width=True):
        if pw == ADMIN_PASSWORD:
            st.session_state.role = "admin"; st.rerun()
        elif pw == USER_PASSWORD:
            st.session_state.role = "user"; st.rerun()
        else:
            st.error("密碼錯誤，請重試")

# ══════════════════════════════════════
#  管理員：上傳清單
# ══════════════════════════════════════
def show_admin_upload():
    st.markdown("### 音樂清單管理")
    st.info("上傳最新的音樂清單 Excel，支援「整份替換」或「新增合併」兩種方式。")
    uploaded = st.file_uploader("選擇 Excel 檔案（.xlsx）", type=["xlsx"])
    if uploaded:
        df_new = pd.read_excel(uploaded)
        st.success(f"讀取成功：共 {len(df_new)} 首")
        st.dataframe(df_new.head(10), use_container_width=True)
        col1, col2 = st.columns(2)
        with col1:
            if st.button("整份替換（取代舊清單）", use_container_width=True):
                st.session_state.music_df = df_new
                st.success("✅ 清單已更新（整份替換）")
        with col2:
            if st.button("新增合併（保留舊資料）", use_container_width=True):
                if st.session_state.music_df is not None:
                    combined = pd.concat([st.session_state.music_df, df_new]).drop_duplicates(
                        subset=["檔案名稱"] if "檔案名稱" in df_new.columns else None
                    ).reset_index(drop=True)
                    st.session_state.music_df = combined
                    st.success(f"✅ 合併完成，目前共 {len(combined)} 首")
                else:
                    st.session_state.music_df = df_new
                    st.success(f"✅ 清單已載入，共 {len(df_new)} 首")
    if st.session_state.music_df is not None:
        st.markdown(f"**目前資料庫：{len(st.session_state.music_df)} 首**")
        st.dataframe(st.session_state.music_df, use_container_width=True, height=300)

# ══════════════════════════════════════
#  關鍵字預篩選（避免清單太大）
# ══════════════════════════════════════
def prefilter(df, product, category, mood):
    keywords = (product + " " + mood).lower().split()
    col_tags  = next((c for c in df.columns if "標籤" in c or "Mood" in c or "Tags" in c), "")
    col_genre = next((c for c in df.columns if "風格" in c or "Genre" in c), "")

    if not keywords or (not col_tags and not col_genre):
        return df.head(200)  # 最多送200首

    def score(row):
        text = " ".join([
            str(row.get(col_tags, "")),
            str(row.get(col_genre, ""))
        ]).lower()
        return sum(1 for k in keywords if k in text)

    df = df.copy()
    df["_score"] = df.apply(score, axis=1)
    top = df[df["_score"] > 0].sort_values("_score", ascending=False).head(150)
    if len(top) < 30:
        top = df.head(150)
    return top.drop(columns=["_score"])

# ══════════════════════════════════════
#  Gemini AI 搜尋
# ══════════════════════════════════════
def search_music(product, category, mood, df):
    col_name  = "檔案名稱" if "檔案名稱" in df.columns else df.columns[1]
    col_genre = next((c for c in df.columns if "風格" in c or "Genre" in c), "")
    col_bpm   = next((c for c in df.columns if "節奏" in c or "BPM" in c), "")
    col_tags  = next((c for c in df.columns if "標籤" in c or "Mood" in c or "Tags" in c), "")

    # 預篩選，控制送出的資料量
    filtered = prefilter(df, product, category, mood)

    lines = []
    for _, row in filtered.iterrows():
        seq   = row.get("序號", row.name + 1)
        name  = row.get(col_name, "")
        genre = row.get(col_genre, "") if col_genre else ""
        bpm   = row.get(col_bpm,   "") if col_bpm   else ""
        tags  = row.get(col_tags,  "") if col_tags  else ""
        lines.append(f"[{seq}] {name} | {genre} | {bpm} | {tags}")

    db_text = "\n".join(lines)

    # 情境描述限制在200字內
    mood_short = mood[:200] if mood else ""

    prompt = f"""你是專業的商業影片選曲顧問。

使用者需求：
- 商品名稱：{product or '未填寫'}
- 商品類型：{category or '不限'}
- 銷售氛圍：{mood_short or '未填寫'}

音樂清單（共{len(filtered)}首）：
{db_text}

請用語意理解比對，挑選最合適的5首。
只回覆JSON陣列，不要任何說明文字：
[{{"seq": 序號, "reason": "20字內說明原因"}}, ...]"""

    url  = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"maxOutputTokens": 512}}).encode()
    req  = urllib.request.Request(url, data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise Exception(f"API 錯誤 {e.code}：{e.read().decode()}")

    text = data["candidates"][0]["content"]["parts"][0]["text"]
    text = text.strip().replace("```json","").replace("```","").strip()
    # 只取第一個 JSON 陣列
    start = text.find("[")
    end   = text.rfind("]") + 1
    return json.loads(text[start:end])

# ══════════════════════════════════════
#  下載 Excel
# ══════════════════════════════════════
def results_to_excel(results, df):
    col_name  = "檔案名稱" if "檔案名稱" in df.columns else df.columns[1]
    col_genre = next((c for c in df.columns if "風格" in c or "Genre" in c), "")
    col_bpm   = next((c for c in df.columns if "節奏" in c or "BPM" in c), "")
    col_tags  = next((c for c in df.columns if "標籤" in c or "Mood" in c or "Tags" in c), "")
    col_url   = next((c for c in df.columns if "連結" in c or "URL" in c or "url" in c), "")
    rows = []
    for i, r in enumerate(results):
        seq = r.get("seq")
        matched = df[df["序號"] == seq] if "序號" in df.columns else pd.DataFrame()
        if matched.empty: continue
        row = matched.iloc[0]
        rows.append({
            "排名":     i + 1,
            "曲名":     row.get(col_name, ""),
            "風格":     row.get(col_genre, "") if col_genre else "",
            "節奏":     row.get(col_bpm,   "") if col_bpm   else "",
            "情境標籤": row.get(col_tags,  "") if col_tags  else "",
            "推薦理由": r.get("reason", ""),
            "試聽連結": row.get(col_url,   "") if col_url   else "",
        })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="推薦歌單")
    buf.seek(0)
    return buf

# ══════════════════════════════════════
#  搜尋畫面
# ══════════════════════════════════════
def show_search():
    st.markdown("## 🎵 商品音樂搜尋")
    if st.session_state.music_df is None:
        st.warning("音樂清單尚未載入。請聯絡管理員上傳清單後再使用。")
        return
    st.caption(f"音樂庫：共 {len(st.session_state.music_df)} 首")

    with st.form("search_form"):
        col1, col2 = st.columns(2)
        with col1:
            product = st.text_input("商品名稱", placeholder="例：歐式沙發、保養乳液")
        with col2:
            category = st.selectbox("商品類型", [
                "不限","家居／傢俱","美妝／保養","3C／科技",
                "食品／飲料","運動／健身","時尚／服飾","汽車／交通","旅遊／生活","其他"
            ])
        mood = st.text_area("銷售氛圍 / 展演情境",
            placeholder="描述你想要的感覺，例如：溫馨居家感、高級精品風、輕快活力…（200字內）",
            height=100, max_chars=200)
        submitted = st.form_submit_button("🔍 搜尋合適音樂", use_container_width=True)

    if submitted:
        if not product and not mood:
            st.error("請至少填寫商品名稱或情境描述")
        elif not GEMINI_KEY:
            st.error("API 金鑰未設定，請聯絡管理員")
        else:
            with st.spinner("AI 正在比對最適合的曲目..."):
                try:
                    picks = search_music(product, category, mood, st.session_state.music_df)
                    st.session_state.results = picks
                    st.session_state.last_query = {"product": product, "category": category, "mood": mood}
                except Exception as e:
                    st.error(f"搜尋發生錯誤：{e}")

    if st.session_state.results:
        df = st.session_state.music_df
        q  = st.session_state.last_query
        col_name  = "檔案名稱" if "檔案名稱" in df.columns else df.columns[1]
        col_genre = next((c for c in df.columns if "風格" in c or "Genre" in c), "")
        col_bpm   = next((c for c in df.columns if "節奏" in c or "BPM" in c), "")
        col_tags  = next((c for c in df.columns if "標籤" in c or "Mood" in c or "Tags" in c), "")
        col_url   = next((c for c in df.columns if "連結" in c or "URL" in c or "url" in c), "")

        st.markdown("---")
        st.markdown(f"**搜尋條件**：{q.get('product','')}　{q.get('category','')}　{q.get('mood','')}")
        st.markdown(f"### 為你推薦 {len(st.session_state.results)} 首最合適的音樂")

        for i, r in enumerate(st.session_state.results):
            seq     = r.get("seq")
            matched = df[df["序號"] == seq] if "序號" in df.columns else pd.DataFrame()
            if matched.empty: continue
            row    = matched.iloc[0]
            name   = str(row.get(col_name,"")).replace("EHS-SUNO,","").replace(".mp3","")
            genre  = row.get(col_genre,"") if col_genre else ""
            bpm    = row.get(col_bpm,  "") if col_bpm   else ""
            tags   = row.get(col_tags, "") if col_tags  else ""
            url    = row.get(col_url,  "") if col_url   else ""
            reason = r.get("reason","")
            label  = "🥇 最推薦" if i == 0 else f"#{i+1}"

            with st.container(border=True):
                c1, c2 = st.columns([5, 1])
                with c1:
                    st.markdown(f"**{label}　{name}**")
                    tag_str = "　".join(
                        ([f"`{genre}`"] if genre else []) +
                        ([f"`{bpm}`"]   if bpm   else []) +
                        [f"`{t.strip()}`" for t in str(tags).split("、") if t.strip()]
                    )
                    st.markdown(tag_str)
                    st.caption(f"推薦理由：{reason}")
                with c2:
                    if url:
                        st.link_button("試聽", str(url), use_container_width=True)

        st.markdown("---")
        excel_buf = results_to_excel(st.session_state.results, df)
        st.download_button(
            label="⬇️ 下載推薦歌單（Excel）",
            data=excel_buf,
            file_name=f"推薦歌單_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

# ══════════════════════════════════════
#  主流程
# ══════════════════════════════════════
if st.session_state.role is None:
    show_login()
else:
    with st.sidebar:
        st.markdown(f"**身份：{'管理員' if st.session_state.role == 'admin' else '一般使用者'}**")
        if st.session_state.music_df is not None:
            st.caption(f"音樂庫：{len(st.session_state.music_df)} 首")
        if st.button("登出"):
            st.session_state.role = None
            st.session_state.results = []
            st.rerun()
        if st.session_state.role == "admin":
            st.markdown("---")
            page = st.radio("功能", ["搜尋音樂", "管理清單"])
        else:
            page = "搜尋音樂"

    if st.session_state.role == "admin" and page == "管理清單":
        show_admin_upload()
    else:
        show_search()
