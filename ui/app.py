import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import asyncio
import json
import plotly.graph_objects as go

import main
from detect.flow import flow_edges
from ingestion.snapshot import init_db
from config import DUCKDB_PATH

st.set_page_config(page_title="Stealth Radar", page_icon="📡", layout="wide")
st.title("📡 Stealth Radar")
st.caption("Forming-team & talent-flow intelligence")
tab1, tab2, tab3 = st.tabs(["🎯 Radar", "🌊 TalentFlow", "📊 Backtest"])

# ─── Radar Tab ────────────────────────────────────────────────────────────────
with tab1:
    anchor_name = st.text_input(
        "Anchor company name",
        placeholder="e.g. Stripe",
        key="anchor_name_radar",
    )
    anchor_url = st.text_input(
        "...or LinkedIn URL",
        placeholder="https://www.linkedin.com/company/stripe",
        key="anchor_url_radar",
    )

    if st.button("Run Radar 🔍"):
        if not anchor_name and not anchor_url:
            st.warning("Please enter a company name or LinkedIn URL.")
        else:
            with st.spinner("Pulling cohort and detecting clusters..."):
                try:
                    results = asyncio.run(
                        main.run(
                            anchor_name=anchor_name or None,
                            anchor_linkedin_url=anchor_url or None,
                        )
                    )
                except Exception as exc:
                    st.error(f"Error running Radar: {exc}")
                    results = []

            if not results:
                st.info("No clusters detected.")
            else:
                st.success(f"Found {len(results)} cluster(s).")
                for cluster, score, tier, features, adjudication, dossier_data in results:
                    expanded = tier in ("High", "Medium")
                    with st.expander(
                        f"[{tier}] score={score:.1f} — {len(cluster)} members",
                        expanded=expanded,
                    ):
                        # Member table
                        member_rows = []
                        for p in cluster:
                            cur = p.current_role
                            member_rows.append({
                                "Name": p.name or "",
                                "Headline": p.headline or "",
                                "Current Title": cur.title if cur else "",
                                "Current Company": cur.company_name if cur else "",
                            })
                        st.dataframe(member_rows, use_container_width=True)

                        # Adjudication
                        st.markdown("**Adjudication**")
                        if isinstance(adjudication, dict):
                            label = adjudication.get("label", adjudication.get("classification", "—"))
                            confidence = adjudication.get("confidence", adjudication.get("confidence_score", "—"))
                            rationale = adjudication.get("rationale", adjudication.get("reasoning", "—"))
                        elif hasattr(adjudication, "label"):
                            label = adjudication.label
                            confidence = getattr(adjudication, "confidence", "—")
                            rationale = getattr(adjudication, "rationale", "—")
                        else:
                            label = str(adjudication)
                            confidence = "—"
                            rationale = "—"

                        col_a, col_b = st.columns(2)
                        col_a.metric("Label", label)
                        col_b.metric("Confidence", confidence)
                        st.caption(f"Rationale: {rationale}")

                        # Dossier
                        if dossier_data:
                            st.markdown("**Dossier**")
                            if isinstance(dossier_data, dict):
                                thesis = dossier_data.get("thesis", "")
                                evidence = dossier_data.get("evidence_timeline", [])
                                rec_action = dossier_data.get("recommended_action", "")
                                urgency = dossier_data.get("urgency", "")
                            elif hasattr(dossier_data, "thesis"):
                                thesis = dossier_data.thesis
                                evidence = getattr(dossier_data, "evidence_timeline", [])
                                rec_action = getattr(dossier_data, "recommended_action", "")
                                urgency = getattr(dossier_data, "urgency", "")
                            else:
                                thesis = str(dossier_data)
                                evidence = []
                                rec_action = ""
                                urgency = ""

                            if thesis:
                                st.markdown(f"**Thesis:** {thesis}")
                            if evidence:
                                st.markdown("**Evidence Timeline:**")
                                for ev in evidence:
                                    st.markdown(f"- {ev}")
                            if rec_action:
                                st.markdown(f"**Recommended Action:** {rec_action}")
                            if urgency:
                                urgency_color = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
                                    str(urgency).lower(), "⚪"
                                )
                                st.markdown(f"**Urgency:** {urgency_color} {urgency}")

# ─── TalentFlow Tab ───────────────────────────────────────────────────────────
with tab2:
    anchor_name_flow = st.text_input(
        "Anchor company name",
        placeholder="e.g. Stripe",
        key="anchor_name_flow",
    )
    anchor_url_flow = st.text_input(
        "...or LinkedIn URL",
        placeholder="https://www.linkedin.com/company/stripe",
        key="anchor_url_flow",
    )

    if st.button("Map TalentFlow 🗺️"):
        if not anchor_name_flow and not anchor_url_flow:
            st.warning("Please enter a company name or LinkedIn URL.")
        else:
            with st.spinner("Mapping talent flows..."):
                try:
                    flow_results = asyncio.run(
                        main.run(
                            anchor_name=anchor_name_flow or None,
                            anchor_linkedin_url=anchor_url_flow or None,
                        )
                    )
                except Exception as exc:
                    st.error(f"Error mapping TalentFlow: {exc}")
                    flow_results = []

            if flow_results:
                # Collect all unique leavers from clusters
                seen_urls = set()
                all_leavers = []
                for cluster, *_ in flow_results:
                    for p in cluster:
                        if p.profile_url not in seen_urls:
                            seen_urls.add(p.profile_url)
                            all_leavers.append(p)

                anchor_label = anchor_name_flow or anchor_url_flow
                edges = flow_edges(all_leavers, anchor_label=anchor_label)

                if not edges:
                    st.info("No flow edges found.")
                else:
                    # Build Sankey
                    dest_names = [e["target"] or f"Unknown ({e['target_id']})" for e in edges]
                    node_labels = [anchor_label] + dest_names
                    node_colors = ["#4C78A8"]  # anchor node

                    # Highlight tiny destinations (headcount <= 25 approximated via stealth signal)
                    for p in all_leavers:
                        cur = p.current_role
                        if cur and cur.headcount_latest is not None and cur.headcount_latest <= 25:
                            pass  # we'll color by matching target name below

                    tiny_companies = set()
                    for p in all_leavers:
                        cur = p.current_role
                        if cur and cur.headcount_latest is not None and cur.headcount_latest <= 25:
                            if cur.company_name:
                                tiny_companies.add(cur.company_name)

                    for dn in dest_names:
                        if dn in tiny_companies:
                            node_colors.append("#E45756")   # red for tiny/stealth
                        else:
                            node_colors.append("#72B7B2")   # teal for normal

                    anchor_idx = 0
                    link_sources = [anchor_idx] * len(edges)
                    link_targets = list(range(1, len(edges) + 1))
                    link_values = [e["weight"] for e in edges]

                    fig = go.Figure(go.Sankey(
                        node=dict(
                            pad=15,
                            thickness=20,
                            line=dict(color="black", width=0.5),
                            label=node_labels,
                            color=node_colors,
                        ),
                        link=dict(
                            source=link_sources,
                            target=link_targets,
                            value=link_values,
                        ),
                    ))
                    fig.update_layout(
                        title_text=f"TalentFlow from {anchor_label}",
                        font_size=12,
                        height=500,
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    # Edge table
                    edge_df = sorted(edges, key=lambda e: e["weight"], reverse=True)
                    st.dataframe(
                        [{"Destination": e["target"], "Weight": e["weight"]} for e in edge_df],
                        use_container_width=True,
                    )
            else:
                st.info("No results found.")

# ─── Backtest Tab ─────────────────────────────────────────────────────────────
with tab3:
    if st.button("Load Backtest Results"):
        try:
            conn = init_db(DUCKDB_PATH)
            rows = conn.execute(
                "SELECT startup, announce_date, horizon_months, caught, score_at_horizon "
                "FROM backtest ORDER BY startup, horizon_months"
            ).fetchall()
            conn.close()
        except Exception as exc:
            st.warning(f"Could not read backtest DB: {exc}")
            rows = []

        if not rows:
            st.info(
                "No backtest data found. Run `python -m backtest.evaluate` first, "
                "then reload this tab."
            )
        else:
            # Recall metrics per horizon
            horizons = [3, 6, 9]
            horizon_stats = {}
            for h in horizons:
                h_rows = [r for r in rows if r[2] == h]
                if h_rows:
                    caught = sum(1 for r in h_rows if r[3])
                    total = len(h_rows)
                    horizon_stats[h] = caught / total if total else 0.0
                else:
                    horizon_stats[h] = None

            st.markdown("### Recall Metrics")
            col3, col6, col9 = st.columns(3)
            col3.metric("Recall @3 months", f"{horizon_stats[3]:.0%}" if horizon_stats[3] is not None else "N/A")
            col6.metric("Recall @6 months", f"{horizon_stats[6]:.0%}" if horizon_stats[6] is not None else "N/A")
            col9.metric("Recall @9 months", f"{horizon_stats[9]:.0%}" if horizon_stats[9] is not None else "N/A")

            # Bar chart
            bar_data = {h: v for h, v in horizon_stats.items() if v is not None}
            if bar_data:
                fig_bar = go.Figure(go.Bar(
                    x=[f"{h}m" for h in bar_data],
                    y=list(bar_data.values()),
                    marker_color=["#4C78A8", "#72B7B2", "#E45756"],
                    text=[f"{v:.0%}" for v in bar_data.values()],
                    textposition="auto",
                ))
                fig_bar.update_layout(
                    title="Recall by Lead-Time Horizon",
                    xaxis_title="Horizon",
                    yaxis_title="Recall",
                    yaxis=dict(range=[0, 1]),
                    height=350,
                )
                st.plotly_chart(fig_bar, use_container_width=True)

            # Full ground truth table
            st.markdown("### Ground Truth Table")
            st.dataframe(
                [
                    {
                        "Startup": r[0],
                        "Announce Date": str(r[1]),
                        "Horizon (months)": r[2],
                        "Caught": "✅" if r[3] else "❌",
                        "Score at Horizon": f"{r[4]:.1f}" if r[4] is not None else "—",
                    }
                    for r in rows
                ],
                use_container_width=True,
            )
