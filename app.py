from __future__ import annotations

import pandas as pd
import streamlit as st

from exporter import build_excel_workbook, dataframe_to_csv_bytes
from exporter import build_named_excel_workbook
from analysis_processor import (
    BCU_GROUPS,
    MISSING_OPTION,
    TTD_GROUPS,
    available_bcu_media_dates,
    available_dates,
    detect_cm_columns,
    detect_columns,
    process_bcu_data,
    process_bcu_media_only,
    process_data,
    read_cm_file,
    read_input_file,
    source_options,
    tracking_tag_options,
)


st.set_page_config(
    page_title="Offline TTD + GA4 Match Analyzer",
    layout="wide",
)


def main() -> None:
    st.title("Offline TTD + GA4 Transaction Match Analyzer")
    st.caption("Local file matching and reporting. No APIs, no cloud processing, no external data sharing.")
    client = st.selectbox("Client / project", ["CARE", "BCU"], index=0)
    analysis_mode = None
    if client == "BCU":
        analysis_mode = st.radio(
            "BCU analysis mode",
            ["TTD + CM only", "TTD + CM -> GA4 overlap"],
            horizontal=True,
        )

    ttd_upload, ga4_upload, cm_upload = render_uploads(client, analysis_mode)
    ga4_required = client == "CARE" or analysis_mode == "TTD + CM -> GA4 overlap"
    if not ttd_upload or (ga4_required and not ga4_upload) or (client == "BCU" and not cm_upload):
        st.info("Upload the required offline files to start.")
        return

    try:
        ttd_loaded = read_input_file(ttd_upload, ga4_mode=False)
        ga4_loaded = read_input_file(ga4_upload, ga4_mode=True) if ga4_upload else None
        cm_loaded = read_cm_file(cm_upload) if client == "BCU" and cm_upload else None
    except Exception as exc:
        st.error(f"Could not read one of the uploaded files: {exc}")
        return

    ttd_df = ttd_loaded.dataframe
    ga4_df = ga4_loaded.dataframe if ga4_loaded else None
    cm_df = cm_loaded.dataframe if cm_loaded else None

    render_file_overview(
        ttd_df,
        ga4_df,
        ttd_loaded.metadata,
        ga4_loaded.metadata if ga4_loaded else None,
        cm_df,
        cm_loaded.metadata if cm_loaded else None,
    )

    detected = detect_columns(ttd_df, ga4_df if ga4_df is not None else pd.DataFrame())
    cm_detected = detect_cm_columns(cm_df) if cm_df is not None else None
    mapping, cm_mapping = render_data_settings(ttd_df, ga4_df, detected, cm_df, cm_detected, ga4_required)
    filters = render_filters(ttd_df, ga4_df, mapping, client, analysis_mode, cm_df, cm_mapping)

    with st.spinner("Matching transactions and building summaries..."):
        if client == "BCU" and analysis_mode == "TTD + CM only":
            result = process_bcu_media_only(
                ttd_df,
                cm_df,
                mapping,
                cm_mapping,
                period_mode=filters["period_mode"],
                date_range=filters["date_range"],
                selected_groups=filters["selected_groups"],
                tracking_tag_filter_mode=filters["tracking_tag_filter_mode"],
                selected_tracking_tags=filters["selected_tracking_tags"],
            )
        elif client == "BCU":
            result = process_bcu_data(
                ttd_df,
                cm_df,
                ga4_df,
                mapping,
                cm_mapping,
                revenue_source=filters["revenue_source"],
                period_mode=filters["period_mode"],
                date_range=filters["date_range"],
                source_filter_mode=filters["source_filter_mode"],
                selected_sources=filters["selected_sources"],
                custom_sources=filters["custom_sources"],
                selected_groups=filters["selected_groups"],
                tracking_tag_filter_mode=filters["tracking_tag_filter_mode"],
                selected_tracking_tags=filters["selected_tracking_tags"],
            )
        else:
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
                tracking_tag_filter_mode=filters["tracking_tag_filter_mode"],
                selected_tracking_tags=filters["selected_tracking_tags"],
            )

    for warning in result["warnings"]:
        st.warning(warning)

    st.caption(f"Date source used for analysis: {result['date_source']}")
    if client == "BCU" and analysis_mode == "TTD + CM only":
        render_bcu_media_only_results(result)
    else:
        render_results(result, client)


def render_uploads(client: str, analysis_mode: str | None) -> tuple[object | None, object | None, object | None]:
    st.subheader("1. Upload files")
    show_ga4 = client == "CARE" or analysis_mode == "TTD + CM -> GA4 overlap"
    column_count = 3 if client == "BCU" and show_ga4 else 2
    columns = st.columns(column_count)
    left = columns[0]
    with left:
        ttd_upload = st.file_uploader("TTD CSV/XLSX", type=["csv", "xlsx", "xls"], key="ttd_upload")
    ga4_upload = None
    cm_upload = None
    if client == "BCU":
        with columns[1]:
            cm_upload = st.file_uploader("CM CSV/XLSX", type=["csv", "xlsx", "xls"], key="cm_upload")
        if show_ga4:
            with columns[2]:
                ga4_upload = st.file_uploader("GA4 CSV/XLSX", type=["csv", "xlsx", "xls"], key="ga4_upload")
    else:
        with columns[1]:
            ga4_upload = st.file_uploader("GA4 CSV/XLSX", type=["csv", "xlsx", "xls"], key="ga4_upload")
    return ttd_upload, ga4_upload, cm_upload


def render_file_overview(
    ttd_df: pd.DataFrame,
    ga4_df: pd.DataFrame | None,
    ttd_meta: dict,
    ga4_meta: dict | None,
    cm_df: pd.DataFrame | None = None,
    cm_meta: dict | None = None,
) -> None:
    st.subheader("File overview")
    visible_count = 1 + int(ga4_df is not None) + int(cm_df is not None)
    columns = st.columns(max(visible_count, 1))
    left = columns[0]
    with left:
        st.markdown(f"**TTD file:** `{ttd_meta['file_name']}`")
        st.write(f"Rows: {len(ttd_df):,}")
        st.write("Detected columns:", list(ttd_df.columns))
        st.dataframe(ttd_df.head(20), use_container_width=True)
    next_idx = 1
    if ga4_df is not None and ga4_meta is not None:
        with columns[next_idx]:
            st.markdown(f"**GA4 file:** `{ga4_meta['file_name']}`")
            st.write(f"Rows after cleanup: {len(ga4_df):,}")
            st.write(f"Skipped metadata/grand-total rows: {ga4_meta.get('skipped_rows', 0):,}")
            if ga4_meta.get("reader_notes"):
                for note in ga4_meta["reader_notes"]:
                    st.caption(note)
            st.write("Detected columns:", list(ga4_df.columns))
            st.dataframe(ga4_df.head(20), use_container_width=True)
        next_idx += 1
    if cm_df is not None and cm_meta is not None:
        with columns[next_idx]:
            st.markdown(f"**CM file:** `{cm_meta['file_name']}`")
            st.write(f"Rows after cleanup: {len(cm_df):,}")
            st.write(f"Skipped metadata rows: {cm_meta.get('skipped_rows', 0):,}")
            st.write("Detected columns:", list(cm_df.columns))
            st.dataframe(cm_df.head(20), use_container_width=True)


def render_data_settings(
    ttd_df: pd.DataFrame,
    ga4_df: pd.DataFrame | None,
    detected: dict[str, str],
    cm_df: pd.DataFrame | None = None,
    cm_detected: dict[str, str] | None = None,
    ga4_required: bool = True,
) -> tuple[dict[str, str], dict[str, str] | None]:
    st.subheader("2. Data settings")
    with st.expander("Column mapping", expanded=True):
        ttd_options = [MISSING_OPTION] + list(ttd_df.columns)
        ga4_options = [MISSING_OPTION] + list(ga4_df.columns) if ga4_df is not None else [MISSING_OPTION]
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
        ga4_id = ga4_source = ga4_revenue = ga4_date = MISSING_OPTION
        if ga4_required:
            with right:
                st.markdown("**GA4 columns**")
                ga4_id = select_column("GA4 transaction ID column", ga4_options, detected["ga4_id"])
                ga4_source = select_column("GA4 source / medium column", ga4_options, detected["ga4_source"])
                ga4_revenue = select_column("GA4 revenue column", ga4_options, detected["ga4_revenue"])
                ga4_date = select_column("GA4 date column", ga4_options, detected["ga4_date"])

        cm_mapping = None
        if cm_df is not None and cm_detected is not None:
            st.markdown("**CM columns**")
            cm_options = [MISSING_OPTION] + list(cm_df.columns)
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                cm_id = select_column("CM transaction ID column", cm_options, cm_detected["cm_id"])
            with c2:
                cm_campaign = select_column("CM campaign column", cm_options, cm_detected["cm_campaign"])
            with c3:
                cm_activity = select_column("CM activity column", cm_options, cm_detected["cm_activity"])
            with c4:
                cm_revenue = select_column("CM revenue column", cm_options, cm_detected["cm_revenue"])
            with c5:
                cm_date = select_column("CM date column", cm_options, cm_detected["cm_date"])
            cm_mapping = {
                "cm_id": cm_id,
                "cm_campaign": cm_campaign,
                "cm_activity": cm_activity,
                "cm_revenue": cm_revenue,
                "cm_date": cm_date,
            }

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
    }, cm_mapping


def select_column(label: str, options: list[str], detected_value: str) -> str:
    index = options.index(detected_value) if detected_value in options else 0
    return st.selectbox(label, options, index=index)


def render_filters(
    ttd_df: pd.DataFrame,
    ga4_df: pd.DataFrame | None,
    mapping: dict[str, str],
    client: str,
    analysis_mode: str | None,
    cm_df: pd.DataFrame | None = None,
    cm_mapping: dict[str, str] | None = None,
) -> dict:
    st.subheader("3. Filters")
    media_only = client == "BCU" and analysis_mode == "TTD + CM only"
    if media_only and cm_df is not None and cm_mapping is not None:
        dates = available_bcu_media_dates(ttd_df, cm_df, mapping, cm_mapping)
    else:
        dates = available_dates(ttd_df, ga4_df if ga4_df is not None else pd.DataFrame(), mapping)
    source_values = source_options(ga4_df, mapping) if ga4_df is not None else []
    tracking_tag_values = tracking_tag_options(ttd_df, mapping)

    left, middle, right = st.columns(3)
    with left:
        if media_only:
            revenue_source = "Media revenue"
            st.caption("Revenue source: TTD/CM media revenue")
        else:
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
        selected_sources: list[str] = []
        custom_sources = ""
        if media_only:
            source_filter_mode = "All sources"
            st.caption("GA4 source filter is hidden for TTD + CM only mode.")
        else:
            source_filter_mode = st.selectbox(
                "GA4 source / medium filter",
                ["All sources", "Direct only", "Selected sources", "Custom source list"],
            )
            if source_filter_mode == "Selected sources":
                selected_sources = st.multiselect("Selected GA4 sources", source_values, default=source_values)
            elif source_filter_mode == "Custom source list":
                custom_sources = st.text_area("Custom source list", placeholder="One source per line or comma separated")

    group_options = BCU_GROUPS if client == "BCU" else TTD_GROUPS
    selected_groups = st.multiselect("Attribution group filter", group_options, default=group_options)
    tracking_tag_filter_mode = st.selectbox(
        "TTD Tracking Tag Name filter",
        ["All tracking tags", "Selected tracking tags"],
    )
    selected_tracking_tags: list[str] = []
    if tracking_tag_filter_mode == "Selected tracking tags":
        preferred = "one-time donation" if client == "BCU" else "care - one time donation"
        default_tags = [tag for tag in tracking_tag_values if tag.lower() == preferred]
        selected_tracking_tags = st.multiselect(
            "Selected TTD tracking tags",
            tracking_tag_values,
            default=default_tags,
        )
        if not selected_tracking_tags:
            st.warning("Select at least one Tracking Tag Name, or switch back to All tracking tags.")

    return {
        "revenue_source": revenue_source,
        "period_mode": period_mode,
        "date_range": date_range,
        "source_filter_mode": source_filter_mode,
        "selected_sources": selected_sources,
        "custom_sources": custom_sources,
        "selected_groups": selected_groups,
        "tracking_tag_filter_mode": tracking_tag_filter_mode,
        "selected_tracking_tags": selected_tracking_tags,
    }


def render_results(result: dict, client: str) -> None:
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
        extra_sheets=bcu_extra_sheets(result) if client == "BCU" else None,
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

    if client == "BCU":
        bcu_cols = st.columns(2)
        with bcu_cols[0]:
            st.download_button(
                "BCU TTD+CM Deduped",
                dataframe_to_csv_bytes(result["bcu_ttd_cm_deduped"]),
                file_name="bcu_ttd_cm_deduped.csv",
                mime="text/csv",
            )
        with bcu_cols[1]:
            st.download_button(
                "BCU TTD+CM Overlap Audit",
                dataframe_to_csv_bytes(result["bcu_ttd_cm_overlap"]),
                file_name="bcu_ttd_cm_overlap_audit.csv",
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

    if client == "BCU":
        st.subheader("BCU TTD + CM deduplication exports")
        bcu_tab_1, bcu_tab_2 = st.tabs(["TTD + CM deduped unique rows", "TTD + CM overlap audit"])
        with bcu_tab_1:
            st.dataframe(result["bcu_ttd_cm_deduped"], use_container_width=True, hide_index=True)
        with bcu_tab_2:
            st.dataframe(result["bcu_ttd_cm_overlap"], use_container_width=True, hide_index=True)


def render_bcu_media_only_results(result: dict) -> None:
    deduped = result["bcu_ttd_cm_deduped"]
    overlap = result["bcu_ttd_cm_overlap"]
    campaign_mapping = result["campaign_mapping"]
    data_quality = result["data_quality"]

    st.subheader("Exports")
    excel_bytes = build_named_excel_workbook(
        {
            "BCU TTD CM Deduped": deduped,
            "BCU TTD CM Overlap": overlap,
            "Campaign Mapping": campaign_mapping,
            "Data Quality": data_quality,
        }
    )
    st.download_button(
        "Download BCU TTD+CM workbook",
        excel_bytes,
        file_name="bcu_ttd_cm_deduped_analysis.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button(
            "BCU TTD+CM Deduped",
            dataframe_to_csv_bytes(deduped),
            file_name="bcu_ttd_cm_deduped.csv",
            mime="text/csv",
        )
    with c2:
        st.download_button(
            "BCU TTD+CM Overlap Audit",
            dataframe_to_csv_bytes(overlap),
            file_name="bcu_ttd_cm_overlap_audit.csv",
            mime="text/csv",
        )
    with c3:
        st.download_button(
            "Data Quality",
            dataframe_to_csv_bytes(data_quality),
            file_name="bcu_ttd_cm_data_quality.csv",
            mime="text/csv",
        )

    status_counts = deduped["TTD / CM status"].value_counts() if "TTD / CM status" in deduped.columns else pd.Series()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Deduped IDs", f"{len(deduped):,}")
    m2.metric("TTD only", f"{int(status_counts.get('TTD only', 0)):,}")
    m3.metric("TTD + CM overlap", f"{int(status_counts.get('TTD + CM overlap', 0)):,}")
    m4.metric("CM only", f"{int(status_counts.get('CM only', 0)):,}")

    tab_deduped, tab_overlap, tab_campaigns, tab_quality = st.tabs(
        ["TTD + CM deduped", "Overlap audit", "Campaign mapping", "Data quality"]
    )
    with tab_deduped:
        st.subheader("BCU TTD + CM deduped unique rows")
        st.dataframe(deduped, use_container_width=True, hide_index=True)
    with tab_overlap:
        st.subheader("BCU TTD + CM overlap audit")
        st.dataframe(overlap, use_container_width=True, hide_index=True)
    with tab_campaigns:
        st.subheader("Campaign mapping review")
        st.dataframe(campaign_mapping, use_container_width=True, hide_index=True)
    with tab_quality:
        st.subheader("Data quality checks")
        st.dataframe(data_quality, use_container_width=True, hide_index=True)


def bcu_extra_sheets(result: dict) -> dict[str, pd.DataFrame]:
    return {
        "BCU TTD CM Deduped": result["bcu_ttd_cm_deduped"],
        "BCU TTD CM Overlap": result["bcu_ttd_cm_overlap"],
    }


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
