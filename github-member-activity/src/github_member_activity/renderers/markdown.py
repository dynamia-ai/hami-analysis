from __future__ import annotations


def render_markdown(summary: dict, statuses: dict) -> str:
    lines = [f"# GitHub public participation — {summary['period']['id']}", "", "公开仓库参与是操作性代理，不构成许可证审计、绩效评分或排名。", "", "## Members", "", "| Member | PRs opened | Issues opened | Issue replies | PRs reviewed | PRs merged | Repositories |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for member in summary["members"]:
        m = member["metrics"]
        lines.append(f"| {member['member_id']} | {m['prs_opened']} | {m['issues_opened']} | {m['issue_replies_created']} | {m['prs_reviewed']} | {m['authored_prs_merged']} | {m['repositories_touched']} |")
    lines.extend(["", "## Source status", ""])
    for row in statuses.get("rows", []):
        lines.append(f"- `{row['member_id']}/{row['source']}`: `{row['status']}`" + (f" (`{row['reason']}`)" if row.get("reason") else ""))
    return "\n".join(lines) + "\n"
