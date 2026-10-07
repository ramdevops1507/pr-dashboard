import altair as alt
import pandas as pd

from jira_client import SprintReport


STATUS_ORDER = ["To do", "In progress", "Done", "Unknown"]
STATUS_COLORS = ["#94a3b8", "#2563eb", "#059669", "#d97706"]


def quality_counts(issues: pd.DataFrame) -> dict[str, int]:
    types = issues["Issue type"].str.strip().str.casefold()
    quality = types.isin(["bug", "defect"])
    return {
        "Bugs": int(types.eq("bug").sum()),
        "Defects": int(types.eq("defect").sum()),
        "Open bugs / defects": int((quality & issues["Category"].ne("Done")).sum()),
        "Resolved bugs / defects": int((quality & issues["Category"].eq("Done")).sum()),
    }


def sprint_quality(reports: list[SprintReport]) -> pd.DataFrame:
    from jira_client import ISSUE_COLUMNS

    return pd.DataFrame([
        {
            "Sprint": f"{report.name} ({report.sprint_id})",
            **quality_counts(pd.DataFrame(report.issues, columns=ISSUE_COLUMNS)),
        }
        for report in reports
    ])


def polish(chart, height=280):
    return (
        chart.properties(height=height)
        .configure_view(stroke=None)
        .configure_axis(gridColor="#e2e8f0", labelColor="#475569", titleColor="#334155")
        .configure_legend(orient="bottom", title=None)
    )


def status_chart(comparison: pd.DataFrame):
    data = comparison.assign(
        Sprint=comparison["Sprint"] + " (" + comparison["Sprint ID"].astype(str) + ")"
    ).melt(id_vars=["Sprint"], value_vars=STATUS_ORDER, var_name="Category", value_name="Issues")
    return polish(
        alt.Chart(data).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Sprint:N", sort=None, axis=alt.Axis(labelAngle=-25)),
            y=alt.Y("Issues:Q", axis=alt.Axis(tickMinStep=1)),
            color=alt.Color("Category:N", scale=alt.Scale(domain=STATUS_ORDER, range=STATUS_COLORS)),
            tooltip=["Sprint:N", "Category:N", "Issues:Q"],
        )
    )


def quality_chart(reports: list[SprintReport]):
    data = sprint_quality(reports).melt(
        id_vars=["Sprint"], value_vars=["Bugs", "Defects"], var_name="Issue type", value_name="Issues"
    )
    return polish(
        alt.Chart(data).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Sprint:N", sort=None, axis=alt.Axis(labelAngle=-25)),
            xOffset="Issue type:N",
            y=alt.Y("Issues:Q", axis=alt.Axis(tickMinStep=1)),
            color=alt.Color("Issue type:N", scale=alt.Scale(domain=["Bugs", "Defects"], range=["#e11d48", "#7c3aed"])),
            tooltip=["Sprint:N", "Issue type:N", "Issues:Q"],
        )
    )


def donut_chart(issues: pd.DataFrame):
    data = issues.groupby("Category").size().reset_index(name="Issues")
    return polish(
        alt.Chart(data).mark_arc(innerRadius=75, outerRadius=115, cornerRadius=4).encode(
            theta="Issues:Q",
            color=alt.Color("Category:N", scale=alt.Scale(domain=STATUS_ORDER, range=STATUS_COLORS)),
            tooltip=["Category:N", "Issues:Q"],
        )
    )


def breakdown_chart(issues: pd.DataFrame, field: str, color: str = "#2563eb", show_labels: bool = False):
    data = issues.groupby(field).size().reset_index(name="Issues")
    x = alt.X("Issues:Q", axis=alt.Axis(tickMinStep=1))
    if show_labels and not data.empty:
        x = alt.X(
            "Issues:Q", axis=alt.Axis(tickMinStep=1),
            scale=alt.Scale(domain=[0, int(data["Issues"].max()) * 1.15]),
        )
    base = alt.Chart(data).encode(
        x=x,
        y=alt.Y(f"{field}:N", sort="-x", axis=alt.Axis(labelLimit=260)),
        tooltip=[f"{field}:N", "Issues:Q"],
    )
    bars = base.mark_bar(color=color, cornerRadiusEnd=5)
    chart = (
        bars + base.mark_text(align="left", dx=6, color="#334155").encode(text=alt.Text("Issues:Q", format="d"))
        if show_labels else bars
    )
    return polish(
        chart,
        height=max(240, len(data) * 28),
    )
