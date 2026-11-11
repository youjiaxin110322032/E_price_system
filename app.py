# -*- coding: utf-8 -*-
"""PChome × MOMO 商品價格比較分析 (Streamlit)

執行方式:
    pip install -r requirements.txt
    streamlit run app.py
"""
import re
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from curl_cffi import requests as cffi_requests  # MOMO 需模擬 Chrome TLS 指紋

# ----------------------------------------------------------------------
# 基本設定
# ----------------------------------------------------------------------
st.set_page_config(page_title="PChome × MOMO 價格比較", page_icon="🛒", layout="wide")

CJK_FONT = "Noto Sans TC, Microsoft JhengHei, PingFang TC, sans-serif"
PLATFORM_COLORS = {"PChome": "#E4002B", "MOMO": "#F5A300"}

# --- PChome ---
PCHOME_API = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
PCHOME_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
SORT_OPTIONS = {
    "銷量 (高到低)": "sale/dc",
    "價格 (低到高)": "prc/ac",
    "價格 (高到低)": "prc/dc",
    "相關度": "rnk/dc",
}

# --- MOMO ---
MOMO_API = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
MOMO_HOME = "https://www.momoshop.com.tw/"
MOMO_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}


# ----------------------------------------------------------------------
# 爬蟲：PChome
# ----------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=600)
def scrape_pchome(keyword: str, pages: int, sort: str, delay: float):
    """回傳 (DataFrame[平台, 頁數, 商品代碼, 品名, 價格], 錯誤訊息列表)。"""
    rows, errors = [], []
    for page in range(1, pages + 1):
        try:
            resp = requests.get(
                PCHOME_API,
                params={"q": keyword, "page": page, "sort": sort},
                headers=PCHOME_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            prods = resp.json().get("prods", []) or []
            if not prods:
                errors.append(f"PChome 第 {page} 頁沒有資料，已停止。")
                break
            for p in prods:
                rows.append(
                    {
                        "平台": "PChome",
                        "頁數": page,
                        "商品代碼": str(p.get("Id", "")).split("-")[0],
                        "品名": p.get("name", ""),
                        "價格": p.get("price", ""),
                    }
                )
        except (requests.RequestException, ValueError) as e:
            errors.append(f"PChome 第 {page} 頁抓取失敗：{e}")
            break
        if page < pages:
            time.sleep(delay)
    return pd.DataFrame(rows), errors


# ----------------------------------------------------------------------
# 爬蟲：MOMO
# ----------------------------------------------------------------------
def build_momo_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
        },
    }


@st.cache_data(show_spinner=False, ttl=600)
def scrape_momo(keyword: str, pages: int, delay: float):
    """回傳 (DataFrame[平台, 頁數, 商品代碼, 品名, 價格], 錯誤訊息列表)。"""
    rows, errors = [], []
    try:
        session = cffi_requests.Session(impersonate="chrome")
        session.get(MOMO_HOME, headers=MOMO_HEADERS)  # 先取得 cookie
    except Exception as e:  # noqa: BLE001
        return pd.DataFrame(), [f"MOMO 連線失敗：{e}"]

    for page in range(1, pages + 1):
        try:
            resp = session.post(MOMO_API, headers=MOMO_HEADERS, json=build_momo_payload(keyword, page))
            if resp.status_code != 200:
                errors.append(f"MOMO 第 {page} 頁失敗，HTTP {resp.status_code}")
                continue
            products = resp.json().get("rtnSearchData", {}).get("goodsInfoList", [])
            if not products:
                errors.append(f"MOMO 第 {page} 頁沒有資料，已停止。")
                break
            for p in products:
                rows.append(
                    {
                        "平台": "MOMO",
                        "頁數": page,
                        "商品代碼": str(p.get("goodsCode", "")),
                        "品名": p.get("goodsName", ""),
                        "價格": p.get("goodsPrice", ""),
                    }
                )
        except Exception as e:  # noqa: BLE001
            errors.append(f"MOMO 第 {page} 頁發生錯誤：{e}")
        if page < pages:
            time.sleep(delay)
    return pd.DataFrame(rows), errors


# ----------------------------------------------------------------------
# 資料處理
# ----------------------------------------------------------------------
def extract_brand(name: str) -> str:
    """優先抓【】或[]內文字當品牌，否則取品名第一個詞。"""
    name = str(name)
    m = re.search(r"[【\[]([^】\]]+)[】\]]", name)
    if m:
        return m.group(1).strip()[:12]
    token = name.strip().split()
    return token[0][:8] if token else "未知"


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["價格"] = pd.to_numeric(
        df["價格"].astype(str).str.replace(r"[^\d.]", "", regex=True), errors="coerce"
    )
    df = df.dropna(subset=["價格"])
    df = df[df["價格"] > 0]
    df["價格"] = df["價格"].astype(int)
    df["品牌"] = df["品名"].apply(extract_brand)
    df["短名稱"] = df["品名"].astype(str).str.slice(0, 18)
    return df.reset_index(drop=True)


def add_price_bins(df: pd.DataFrame, n_bins: int) -> tuple[pd.DataFrame, list]:
    """兩個平台共用同一組價格區間（才能比較）。
    以 5%~95% 分位數決定範圍，超出的歸入最後一個「xxx+」區間，避免極端值壓縮分箱。"""
    df = df.copy()
    lo = int(np.floor(df["價格"].min()))
    hi = int(np.ceil(df["價格"].quantile(0.95)))
    cuts = np.unique(np.round(np.linspace(lo, max(hi, lo + 1), n_bins + 1)).astype(int))
    if len(cuts) < 3:  # 價格幾乎相同
        df["價格區間"] = "全部"
        return df, ["全部"]
    bins = list(cuts[:-1]) + [np.inf]
    labels = [f"{cuts[i]}–{cuts[i + 1] - 1}" for i in range(len(cuts) - 2)] + [f"{cuts[-2]}+"]
    df["價格區間"] = pd.cut(df["價格"], bins=bins, labels=labels, right=False)
    df["價格區間"] = pd.Categorical(df["價格區間"], categories=labels, ordered=True)
    return df, labels


def add_merged_brand(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    df = df.copy()
    top = df["品牌"].value_counts().head(top_n).index
    df["品牌(合併)"] = df["品牌"].where(df["品牌"].isin(top), "其他")
    return df


# ----------------------------------------------------------------------
# 圖表
# ----------------------------------------------------------------------
def style(fig, title: str, height: int | None = None):
    fig.update_layout(
        title=dict(text=title, font=dict(size=20)),
        font=dict(family=CJK_FONT, size=14),
        legend_title_text="",
    )
    if height:
        fig.update_layout(height=height)
    return fig


def line_chart(df: pd.DataFrame, kw: str, n_line: int):
    parts = []
    for plat, g in df.groupby("平台", sort=False):
        g = g.head(n_line).copy()
        g["序號"] = range(1, len(g) + 1)
        parts.append(g)
    sub = pd.concat(parts, ignore_index=True)
    fig = px.line(
        sub, x="序號", y="價格", color="平台", markers=True,
        hover_data={"品名": True}, color_discrete_map=PLATFORM_COLORS,
    )
    for plat, mean_p in df.groupby("平台")["價格"].mean().items():
        fig.add_hline(
            y=mean_p, line_dash="dash", line_color=PLATFORM_COLORS.get(plat, "gray"),
            annotation_text=f"{plat} 平均 {mean_p:,.0f}", annotation_position="top left",
        )
    fig.update_xaxes(title="商品序號（依搜尋排序）")
    fig.update_yaxes(title="價格 (NT$)")
    return style(fig, f"「{kw}」兩平台商品售價比較")


def pie_chart(df: pd.DataFrame, platform: str, labels: list, color_map: dict):
    g = df[df["平台"] == platform]
    counts = g["價格區間"].value_counts().reindex(labels, fill_value=0).reset_index()
    counts.columns = ["價格區間", "商品數"]
    counts = counts[counts["商品數"] > 0]
    fig = px.pie(
        counts, names="價格區間", values="商品數", hole=0.3,
        color="價格區間", color_discrete_map=color_map,
    )
    fig.update_traces(textinfo="label+percent", sort=False)
    return style(fig, f"{platform}：價格區間占比")


def sunburst_chart(df: pd.DataFrame, kw: str, path: list):
    grp = df.groupby(path, observed=True).size().reset_index(name="商品數")
    fig = px.sunburst(grp, path=path, values="商品數")
    fig.update_traces(textinfo="label+percent parent")
    return style(fig, f"「{kw}」旭日圖：{' → '.join(path)}", height=700)


def bar_stats(df: pd.DataFrame, kw: str):
    stats = (
        df.groupby("平台")["價格"]
        .agg(平均="mean", 中位數="median", 最低="min", 最高="max")
        .round(0)
        .reset_index()
        .melt(id_vars="平台", var_name="統計量", value_name="價格")
    )
    fig = px.bar(
        stats, x="統計量", y="價格", color="平台", barmode="group", text_auto=",.0f",
        color_discrete_map=PLATFORM_COLORS,
    )
    fig.update_yaxes(title="價格 (NT$)")
    return style(fig, f"「{kw}」價格統計比較")


def bar_bins(df: pd.DataFrame, labels: list, kw: str, as_percent: bool):
    cnt = df.groupby(["平台", "價格區間"], observed=False).size().reset_index(name="商品數")
    if as_percent:
        cnt["占比(%)"] = cnt["商品數"] / cnt.groupby("平台")["商品數"].transform("sum") * 100
        y, fmt = "占比(%)", ".1f"
    else:
        y, fmt = "商品數", "d"
    fig = px.bar(
        cnt, x="價格區間", y=y, color="平台", barmode="group", text_auto=fmt,
        category_orders={"價格區間": labels}, color_discrete_map=PLATFORM_COLORS,
    )
    return style(fig, f"「{kw}」各價格區間{'占比' if as_percent else '商品數'}")


def bar_brands(df: pd.DataFrame, kw: str, top_n: int):
    fig_rows = []
    for plat, g in df.groupby("平台"):
        vc = g["品牌"].value_counts().head(top_n)
        fig_rows.append(pd.DataFrame({"平台": plat, "品牌": vc.index, "商品數": vc.values}))
    data = pd.concat(fig_rows, ignore_index=True)
    fig = px.bar(
        data, x="商品數", y="品牌", color="平台", barmode="group", orientation="h",
        color_discrete_map=PLATFORM_COLORS, text_auto=True,
    )
    fig.update_yaxes(categoryorder="total ascending")
    return style(fig, f"「{kw}」各平台前 {top_n} 大品牌（商品數）", height=max(450, top_n * 45))


# ----------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------
st.title("🛒 PChome × MOMO 商品價格比較")
st.caption("輸入產品名稱與爬取頁數，同時爬取兩大平台，並以折線圖、圓餅圖、旭日圖、柱狀圖比較價格分布。")

with st.sidebar:
    st.header("搜尋條件")
    keyword = st.text_input("產品名稱", value="行動電源")
    pages = st.number_input("爬取頁數（每個平台）", min_value=1, max_value=20, value=3, step=1)
    platforms = st.multiselect("比較平台", ["PChome", "MOMO"], default=["PChome", "MOMO"])
    sort_label = st.selectbox("PChome 排序方式", list(SORT_OPTIONS.keys()))
    delay = st.slider("每頁間隔秒數（避免被擋）", 0.5, 10.0, 1.0, 0.5)

    st.header("圖表設定")
    n_bins = st.slider("價格區間數量", 3, 10, 6)
    n_line = st.slider("折線圖每平台顯示筆數", 10, 500, 70, 10)
    top_brands = st.slider("旭日圖／品牌圖顯示前幾大品牌", 3, 20, 8)
    run = st.button("開始爬取", type="primary", use_container_width=True)

if run:
    kw_in = keyword.strip()
    if not kw_in:
        st.warning("請輸入產品名稱。")
        st.stop()
    if not platforms:
        st.warning("請至少選擇一個平台。")
        st.stop()

    frames, all_errors = [], []
    with st.spinner(f"正在爬取「{kw_in}」前 {int(pages)} 頁…"):
        if "PChome" in platforms:
            d, e = scrape_pchome(kw_in, int(pages), SORT_OPTIONS[sort_label], delay)
            frames.append(d)
            all_errors += e
        if "MOMO" in platforms:
            d, e = scrape_momo(kw_in, int(pages), delay)
            frames.append(d)
            all_errors += e

    frames = [f for f in frames if not f.empty]
    st.session_state["errors"] = all_errors
    st.session_state["keyword"] = kw_in
    if frames:
        st.session_state["raw"] = pd.concat(frames, ignore_index=True)
    else:
        st.session_state.pop("raw", None)
        for msg in all_errors:
            st.warning(msg)
        st.error("沒有取得任何資料，可能被網站阻擋或關鍵字無結果。")

if "raw" not in st.session_state:
    st.info("請在左側輸入條件後按「開始爬取」。")
    st.stop()

raw = st.session_state["raw"]
kw = st.session_state["keyword"]

for msg in st.session_state.get("errors", []):
    st.warning(msg)

df = clean_data(raw)
if df.empty:
    st.error("資料中沒有可用的價格欄位。")
    st.stop()

df, bin_labels = add_price_bins(df, n_bins)
df = add_merged_brand(df, top_brands)
plats = [p for p in ["PChome", "MOMO"] if p in df["平台"].unique()]
palette = px.colors.qualitative.Set2 + px.colors.qualitative.Pastel
bin_color_map = {lab: palette[i % len(palette)] for i, lab in enumerate(bin_labels)}

# ---- 統計摘要（每個平台一列）----
for plat in plats:
    g = df[df["平台"] == plat]
    st.subheader(plat)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("商品數", f"{len(g):,}")
    c2.metric("平均價格", f"NT$ {g['價格'].mean():,.0f}")
    c3.metric("中位數", f"NT$ {g['價格'].median():,.0f}")
    c4.metric("最高價格", f"NT$ {g['價格'].max():,}")
    c5.metric("最低價格", f"NT$ {g['價格'].min():,}")

if len(plats) == 2:
    a = df[df["平台"] == plats[0]]["價格"].mean()
    b = df[df["平台"] == plats[1]]["價格"].mean()
    cheaper = plats[0] if a < b else plats[1]
    st.info(f"💡 平均價格較低的平台：**{cheaper}**（差距約 NT$ {abs(a - b):,.0f}，"
            f"{abs(a - b) / max(a, b) * 100:.1f}%）。僅供參考：搜尋排序與商品組成不同，會影響平均值。")

tab_line, tab_pie, tab_sun, tab_bar, tab_data = st.tabs(
    ["📈 折線圖", "🥧 圓餅圖", "☀️ 旭日圖", "📊 柱狀圖", "📋 資料表"]
)

with tab_line:
    st.plotly_chart(line_chart(df, kw, n_line), use_container_width=True)

with tab_pie:
    cols = st.columns(len(plats))
    for col, plat in zip(cols, plats):
        with col:
            st.plotly_chart(pie_chart(df, plat, bin_labels, bin_color_map), use_container_width=True)

with tab_sun:
    mode = st.radio(
        "旭日圖層級",
        ["平台 → 價格區間 → 品牌", "價格區間 → 品牌（依平台分開看）"],
        horizontal=True,
    )
    if mode.startswith("平台"):
        st.plotly_chart(sunburst_chart(df, kw, ["平台", "價格區間", "品牌(合併)"]), use_container_width=True)
    else:
        cols = st.columns(len(plats))
        for col, plat in zip(cols, plats):
            with col:
                st.plotly_chart(
                    sunburst_chart(df[df["平台"] == plat], f"{plat}-{kw}", ["價格區間", "品牌(合併)"]),
                    use_container_width=True,
                )

with tab_bar:
    st.plotly_chart(bar_stats(df, kw), use_container_width=True)
    as_pct = st.toggle("以占比(%)顯示（兩平台商品數不同時較公平）", value=True)
    st.plotly_chart(bar_bins(df, bin_labels, kw, as_pct), use_container_width=True)
    st.plotly_chart(bar_brands(df, kw, top_brands), use_container_width=True)

with tab_data:
    show_cols = ["平台", "頁數", "商品代碼", "品名", "價格", "價格區間", "品牌"]
    view = df[show_cols]
    plat_filter = st.multiselect("篩選平台", plats, default=plats)
    view = view[view["平台"].isin(plat_filter)]
    st.dataframe(view, use_container_width=True, hide_index=True)
    st.download_button(
        "下載 CSV",
        data=view.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"比價_{kw}_{datetime.now():%Y%m%d}.csv",
        mime="text/csv",
    )
