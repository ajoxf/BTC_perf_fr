"""
SD Touch Analysis Page for Streamlit
Tracks when spread touches various SD levels and whether it returns to mean.
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timezone

# Page config
st.set_page_config(
    page_title="SD Analysis",
    page_icon="📊",
    layout="wide"
)

# Get monitor from main app
try:
    from streamlit_app import monitor, db
except ImportError:
    st.error("Could not import monitor. Please run from main app.")
    st.stop()

# Title and status
st.title("📊 SD Touch Analysis")

# Check if sd_tracker exists
if not hasattr(monitor, 'sd_tracker'):
    st.error("SD Tracker not initialized. Please restart the app.")
    st.stop()

sd_tracker = monitor.sd_tracker

# Status and controls row
col1, col2, col3, col4 = st.columns([2, 1, 1, 1])

with col1:
    is_paused = sd_tracker.is_paused()
    if is_paused:
        st.warning("● Tracking Paused")
    else:
        st.success("● Tracking Active")

with col2:
    if st.button("Pause" if not is_paused else "Resume", use_container_width=True):
        sd_tracker.toggle_pause()
        st.rerun()

with col3:
    if st.button("Reset All", type="secondary", use_container_width=True):
        sd_tracker.reset()
        st.success("All records cleared!")
        st.rerun()

with col4:
    days = st.selectbox("Time Period", [1, 7, 14, 30], index=1, format_func=lambda x: f"Last {x} days")

st.divider()

# Tabs
tab1, tab2, tab3 = st.tabs(["Summary by SD Level", "Daily Breakdown", "Recent Touches"])

# Summary Tab
with tab1:
    summary_data = sd_tracker.get_summary(days=days)

    if summary_data:
        df = pd.DataFrame(summary_data)

        # Format the dataframe for display
        st.subheader("Summary by SD Level")

        for _, row in df.iterrows():
            with st.container(border=True):
                cols = st.columns([1, 1, 1, 1, 1, 1])

                with cols[0]:
                    st.metric("SD Level", f"{row['sd_level']}σ {row['direction']}")

                with cols[1]:
                    st.metric("Total Touches", row['total_touches'])

                with cols[2]:
                    st.metric("Reached Mean", f"{row['reached_mean']} ({row['success_rate']:.1f}%)")

                with cols[3]:
                    st.metric("Avg Gross Profit", f"${row['avg_gross_profit']:.2f}")

                with cols[4]:
                    st.metric("Avg Cost", f"${row['avg_actual_cost']:.4f}")

                with cols[5]:
                    color = "normal" if row['avg_net_profit'] >= 0 else "inverse"
                    profitable = "YES" if row['profitable'] else "NO"
                    st.metric(
                        f"Avg Net Profit ({profitable})",
                        f"${row['avg_net_profit']:.2f}",
                        delta_color=color
                    )

                # Delete button for this level
                if st.button(f"Delete {row['sd_level']}σ {row['direction']}", key=f"del_{row['sd_level']}_{row['direction']}"):
                    sd_tracker.delete_by_level(row['sd_level'], row['direction'])
                    st.rerun()
    else:
        st.info("No SD touch data yet. Waiting for spread to touch SD levels...")

# Daily Tab
with tab2:
    daily_data = sd_tracker.get_daily(days=days)

    if daily_data:
        st.subheader("Daily Breakdown")

        for day in daily_data:
            with st.expander(f"**{day['date']}** - {day['total_touches']} touches, {day['reached_mean']} reached mean"):
                cols = st.columns(4)

                with cols[0]:
                    st.metric("Total Touches", day['total_touches'])

                with cols[1]:
                    st.metric("Reached Mean", day['reached_mean'])

                with cols[2]:
                    st.metric("Pending", day['pending'])

                with cols[3]:
                    color = "normal" if day['total_net_profit'] >= 0 else "inverse"
                    st.metric("Net Profit", f"${day['total_net_profit']:.2f}", delta_color=color)

                # Show breakdown by level
                if day.get('by_level'):
                    st.caption("By Level:")
                    level_cols = st.columns(len(day['by_level']))
                    for i, (level, stats) in enumerate(day['by_level'].items()):
                        with level_cols[i]:
                            st.write(f"{level}: {stats['reached']}/{stats['touches']}")
    else:
        st.info("No daily data available.")

# Recent Touches Tab
with tab3:
    recent_data = sd_tracker.get_recent(days=days)

    if recent_data:
        st.subheader("Recent Touches")

        # Convert to dataframe for easier display
        rows = []
        for r in recent_data:
            entry_cost = (r.get('entry_spot_spread') or 0) + (r.get('entry_futures_spread') or 0)
            exit_cost = (r.get('exit_spot_spread') or 0) + (r.get('exit_futures_spread') or 0)
            gross_profit = r.get('potential_profit') or 0
            net_profit = gross_profit - entry_cost - exit_cost

            rows.append({
                'ID': r['id'],
                'Date': r['touch_date'],
                'Time': r['touch_time'],
                'SD': f"{r['sd_level']}σ",
                'Direction': r['direction'],
                'Entry Spread': f"${r['touch_spread']:.2f}",
                'Exit Spread': f"${r['spread_at_mean']:.2f}" if r.get('spread_at_mean') else '-',
                'Gross Profit': f"${gross_profit:.2f}",
                'Entry Cost': f"${entry_cost:.4f}",
                'Exit Cost': f"${exit_cost:.4f}",
                'Net Profit': f"${net_profit:.2f}",
                'Status': r['status']
            })

        df = pd.DataFrame(rows)

        # Selection for deletion
        selected_ids = st.multiselect(
            "Select touches to delete:",
            options=[r['ID'] for r in rows],
            format_func=lambda x: f"#{x}"
        )

        if selected_ids:
            if st.button(f"Delete {len(selected_ids)} selected", type="primary"):
                sd_tracker.delete_by_ids(selected_ids)
                st.success(f"Deleted {len(selected_ids)} records")
                st.rerun()

        # Display table
        st.dataframe(
            df.drop(columns=['ID']),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No recent touches recorded.")

# Footer with explanation
st.divider()
with st.expander("How SD Touch Analysis Works"):
    st.markdown("""
    **SD Touch Tracking** monitors when the spread (futures - spot price) reaches various standard deviation levels
    and tracks whether it subsequently returns to the mean.

    **Tracked SD Levels:** 4.0σ, 3.5σ, 3.0σ, 2.5σ, 2.0σ (both upper/SHORT and lower/LONG)

    **Metrics Explained:**
    - **Entry Spread**: The spread value when the SD level was touched
    - **Exit Spread**: The spread value when it returned to mean (zscore crossed 0)
    - **Gross Profit**: Theoretical profit from spread reversion
    - **Entry/Exit Cost**: Bid-ask spread at entry and exit (trading cost)
    - **Net Profit**: Gross profit minus trading costs
    - **Profitable**: Whether the average net profit is positive (trading costs factored in)

    **5-minute cooldown** between duplicate touches of the same level to avoid spam.
    """)
