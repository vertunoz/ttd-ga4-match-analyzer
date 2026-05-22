from __future__ import annotations

import pandas as pd
import streamlit as st

from exporter import build_excel_workbook, dataframe_to_csv_bytes
from processor import (
    MISSING_OPTION,
    TTD_GROUPS,
    available_dates,
    detect_columns,
    process_data,
    read_input_file,
    source_options,
)


st.set_page_config(
    page_title="Offline TTD + GA4 Match Analyzer",
    layout="wide",
)


def main() -> None:
    st.title("Offline TTD + GA4 Transaction Match Analyzer")
    st.caption("Local file matching and reporting. No APIs, no cloud processing, no external data sharing.")

    ttd_upload, ga4_upload = render_uploads()
    if not ttd_upload or not ga4_upload:
        st.info("Upload a TTD transaction report and a GA4 transaction export to start.")
        return

    try:
        ttd_loaded = read_input_file(ttd_upload, ga4_mode=False)
        ga4_loaded = read_input_file(ga4_upload, ga4_mode=True)
    except Exception as exc:
        st.error(f"Could not read one of the uploaded files: {exc}")
        return

    ttd_df = ttd_loaded.dataframe
    ga4_df = ga4_loaded.dataframe

    render_file_overview(ttd_df, ga4_df, ttd_loaded.metadata, ga4_loaded.metadata)

    detected = detect_columns(ttd_df, ga4_df)
    mapping = render_data_settings(ttd_df, ga4_df, detected)
    filters = render_filters(ttd_df, ga4_df, mapping)

    with st.spinner("Matching transactions and building summaries..."):
        result = process_data(
            ttd_df,
            ga4_df,
            mapping,
            revenue_source=filters["revenue_source"],
            period_mode=filters["period_mode"],
            date_range=filters["date_range"],
            source_filter_mode=filters["source_filter_mode"],
            selected_sources=filters["selected_sources"],
            custom_sources=filters["custom_sources"],
            selected_groups=filters["selected_groups"],
        )

    for warning in result["warnings"]:
        st.warning(warning)

    st.caption(f"Date source used for matched transaction period logic: {result['date_source']}")
    render_results(result)


def render_uploads() -> tuple[object | None, object | None]:
    st.subheader("1. Upload files")
    left, right = st.columns(2)
    with left:
        ttd_upload = st.file_uploader("TTD CSV/XLSX", type=["csv", "xlsx", "xls"], key="ttd_upload")
    with right:
        ga4_upload = st.file_uploader("GA4 CSV/XLSX", type=["csv", "xlsx", "xls"], key="ga4_upload")
    return ttd_upload, ga4_upload


def render_file_overview(
    ttd_df: pd.DataFrame,
    ga4_df: pd.DataFrame,
    ttd_meta: dict,
    ga4_meta: dict,
) -> None:
    st.subheader("File overview")
    left, right = st.columns(2)
    with left:
        st.markdown(f"**TTD file:** `{ttd_meta['file_name']}`")
        st.write(f"Rows: {len(ttd_df):,}")
        st.write("Detected columns:", list(ttd_df.columns))
        st.dataframe(ttd_df.head(20), use_container_width=True)
    with right:
        st.markdown(f"**GA4 file:** `{ga4_meta['file_name']}`")
        st.write(f"Rows after cleanup: {len(ga4_df):,}")
        st.write(f"Skipped metadata/grand-total rows: {ga4_meta.get('skipped_rows', 0):,}")
        if ga4_meta.get("reader_notes"):
            for note in ga4_meta["reader_notes"]:
                st.caption(note)
        st.write("Detected columns:", list(ga4_df.columns))
        st.dataframe(ga4_df.head(20), use_container_width=True)


def render_data_settings(
    ttd_df: pd.DataFrame, ga4_df: pd.DataFrame, detected: dict[str, str]
) -> dict[str, str]:
    st.subheader("2. Data settings")
    with st.expander("Column mapping", expanded=True):
        ttd_options = [MISSING_OPTION] + list(ttd_df.columns)
        ga4_options = [MISSING_OPTION] + list(ga4_df.columns)
        left, right = st.columns(2)
        with left:
            st.markdown("**TTD columns**")
            ttd_id = select_column("TTD transaction ID column", ttd_options, detected["ttd_id"])
            ttd_campaign = select_column("TTD campaign column", ttd_options, detected["ttd_campaign"])
            ttd_tracking = select_column("TTD tracking tag column", ttd_options, detected["ttd_tracking"])
            ttd_referrer = select_column("TTD referrer URL column", ttd_options, detected["ttd_referrer"])
            ttd_conversion_id = select_column(
                "TTD conversion ID column", ttd_options, detected["ttd_conversion_id"]
            )
            ttd_revenue = select_column("TTD revenue column", ttd_options, detected["ttd_revenue"])
            ttd_time = select_column("TTD conversion time column", ttd_options, detected["ttd_time"])
        with right:
            st.markdown("**GA4 columns**")
            ga4_id = select_column("GA4 transaction ID column", ga4_options, detected["ga4_id"])
            ga4_source = select_column("GA4 source / medium column", ga4_options, detected["ga4_source"])
            ga4_revenue = select_column("GA4 revenue column", ga4_options, detected["ga4_revenue"])
            ga4_date = select_column("GA4 date column", ga4_options, detected["ga4_date"])

    return {
        "ttd_id": ttd_id,
        "ttd_campaign": ttd_campaign,
        "ttd_tracking": ttd_tracking,
        "ttd_referrer": ttd_referrer,
        "ttd_conversion_id": ttd_conversion_id,
        "ttd_revenue": ttd_revenue,
        "ttd_time": ttd_time,
        "ga4_id": ga4_id,
        "ga4_source": ga4_source,
        "ga4_revenue": ga4_revenue,
        "ga4_date": ga4_date,
    }


def select_column(label: str, options: list[str], detected_value: str) -> str:
    index = options.index(detected_value) if detected_value in options else 0
    return st.selectbox(label, options, index=index)


def render_filters(ttd_df: pd.DataFrame, ga4_df: pd.DataFrame, mapping: dict[str, str]) -> dict:
    st.subheader("3. Filters")
    dates = available_dates(ttd_df, ga4_df, mapping)
    source_values = source_options(ga4_df, mapping)

    left, middle, right = st.columns(3)
    with left:
        revenue_source = st.radio("Revenue source for summary", ["GA4 revenue", "TTD revenue"], horizontal=True)
        period_mode = st.radio("Period mode", ["Full period", "Weekly"], horizontal=True)
    with middle:
        if dates:
            default_start, default_end = dates
            selected_range = st.date_input(
                "Date range",
                value=(default_start.date(), default_end.date()),
                min_value=default_start.date(),
                max_value=default_end.date(),
            )
            if isinstance(selected_range, tuple) and len(selected_range) == 2:
                date_range = (pd.Timestamp(selected_range[0]), pd.Timestamp(selected_range[1]))
            else:
                date_range = None
        else:
            st.info("No parseable date range detected.")
            date_range = None
    with right:
        source_filter_mode = st.selectbox(
            "GA4 source / medium filter",
            ["All sources", "Direct only", "Selected sources", "Custom source list"],
        )
        selected_sources: list[str] = []
        custom_sources = ""
        if source_filter_mode == "Selected sources":
            selected_sources = st.multiselect("Selected GA4 sources", source_values, default=source_values)
        elif source_filter_mode == "Custom source list":
            custom_sources = st.text_area("Custom source list", placeholder="One source per line or comma separated")

    selected_groups = st.multiselect("Attribution group filter", TTD_GROUPS, default=TTD_GROUPS)

    return {
        "revenue_source": revenue_source,
        "period_mode": period_mode,
        "date_range": date_range,
        "source_filter_mode": source_filter_mode,
        "selected_sources": selected_sources,
        "custom_sources": custom_sources,
        "selected_groups": selected_groups,
    }


def render_results(result: dict) -> None:
    summary = result["summary"]
    matched = result["matched_transactions"]
    raw_matched = result["raw_matched_rows"]
    campaign_mapping = result["campaign_mapping"]
    data_quality = result["data_quality"]
    ttd_unmatched = result["ttd_unmatched"]
    ga4_unmatched = result["ga4_unmatched"]

    st.subheader("Exports")
    excel_bytes = build_excel_workbook(
        summary,
        matched,
        raw_matched,
        campaign_mapping,
        ttd_unmatched,
        ga4_unmatched,
        data_quality,
    )
    st.download_button(
        "Download Excel workbook",
        excel_bytes,
        file_name="ttd_ga4_match_analysis.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    csv_cols = st.columns(5)
    csv_exports = [
        ("Summary", summary, "summary.csv"),
        ("Matched Transactions", matched, "matched_transactions.csv"),
        ("Campaign Mapping", campaign_mapping, "campaign_mapping.csv"),
        ("TTD Unmatched", ttd_unmatched, "ttd_unmatched.csv"),
        ("GA4 Unmatched", ga4_unmatched, "ga4_unmatched.csv"),
    ]
    for col, (label, df, file_name) in zip(csv_cols, csv_exports):
        with col:
            st.download_button(
                label,
                dataframe_to_csv_bytes(df),
                file_name=file_name,
                mime="text/csv",
            )

    tab_summary, tab_campaigns, tab_matched, tab_quality, tab_unmatched = st.tabs(
        [
            "Main summary",
            "Campaign mapping review",
            "Detailed matched transactions",
            "Data quality checks",
            "Unmatched IDs",
        ]
    )

    with tab_summary:
        st.subheader("5. Main summary")
        st.dataframe(format_summary(summary), use_container_width=True, hide_index=True)
    with tab_campaigns:
        st.subheader("4. Campaign mapping review")
        st.dataframe(campaign_mapping, use_container_width=True, hide_index=True)
    with tab_matched:
        st.subheader("6. Detailed matched transactions")
        st.dataframe(matched, use_container_width=True, hide_index=True)
        with st.expander("Raw matched rows"):
            st.dataframe(raw_matched, use_container_width=True, hide_index=True)
    with tab_quality:
        st.subheader("7. Data quality checks")
        st.dataframe(data_quality, use_container_width=True, hide_index=True)
    with tab_unmatched:
        left, right = st.columns(2)
        with left:
            st.markdown("**TTD IDs not found in GA4**")
            st.dataframe(ttd_unmatched, use_container_width=True, hide_index=True)
        with right:
            st.markdown("**GA4 IDs not found in TTD**")
            st.dataframe(ga4_unmatched, use_container_width=True, hide_index=True)


def format_summary(summary: pd.DataFrame):
    return summary.style.format(
        {
            "Conversions": "{:,.0f}",
            "Revenue": "${:,.2f}",
            "Share of total conversions": "{:.1%}",
            "Share of total revenue": "{:.1%}",
        },
        na_rep="",
    )


if __name__ == "__main__":
    main()
