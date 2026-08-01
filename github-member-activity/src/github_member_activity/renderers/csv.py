from __future__ import annotations

import csv
import io

HEADER = "member_id,github_login,prs_opened,issues_opened,issue_replies_created,issues_replied_to,prs_reviewed,authored_prs_merged,repositories_touched,owners_touched,external_repositories_touched,repositories_accepting_prs,commit_contributions,commit_days,repositories_with_commits"


def render_csv(members: list[dict]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(HEADER.split(","))
    fields = HEADER.split(",")
    for member in sorted(members, key=lambda item: item["member_id"]):
        row = {"member_id": member["member_id"], "github_login": member["github_login"], **member["metrics"]}
        writer.writerow(["" if row.get(field) is None else row.get(field) for field in fields])
    return output.getvalue()
