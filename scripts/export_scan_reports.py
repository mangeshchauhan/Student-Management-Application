#!/usr/bin/env python3
"""Export SonarQube issues and Trivy image vulnerabilities as CSV."""

import argparse
import base64
import csv
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SONAR_FIELDS = [
    "commit",
    "projectKey",
    "issueKey",
    "rule",
    "severity",
    "type",
    "component",
    "line",
    "status",
    "message",
    "creationDate",
    "updateDate",
]

TRIVY_FIELDS = [
    "commit",
    "image",
    "target",
    "class",
    "type",
    "vulnerabilityId",
    "package",
    "installedVersion",
    "fixedVersion",
    "severity",
    "title",
    "primaryUrl",
]


def write_csv(path: str, fields: list[str], rows: list[dict[str, object]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def export_sonar(args: argparse.Namespace) -> None:
    token_value = os.environ.get("SONAR_REPORT_TOKEN")
    if not token_value:
        raise ValueError("SONAR_REPORT_TOKEN environment variable is required")
    token = base64.b64encode(f"{token_value}:".encode("utf-8")).decode("ascii")
    headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}
    issues: list[dict[str, object]] = []
    page = 1
    page_size = 500

    while True:
        query = urlencode(
            {"componentKeys": args.project_key, "p": page, "ps": page_size}
        )
        request = Request(
            f"{args.host.rstrip('/')}/api/issues/search?{query}", headers=headers
        )
        with urlopen(request, timeout=60) as response:
            payload = json.load(response)

        page_issues = payload.get("issues")
        paging = payload.get("paging")
        if not isinstance(page_issues, list) or not isinstance(paging, dict):
            raise ValueError("SonarQube returned an unexpected issues API response")

        for issue in page_issues:
            issues.append(
                {
                    "commit": args.commit,
                    "projectKey": args.project_key,
                    "issueKey": issue.get("key", ""),
                    "rule": issue.get("rule", ""),
                    "severity": issue.get("severity", ""),
                    "type": issue.get("type", ""),
                    "component": issue.get("component", ""),
                    "line": issue.get("line", ""),
                    "status": issue.get("status", ""),
                    "message": issue.get("message", ""),
                    "creationDate": issue.get("creationDate", ""),
                    "updateDate": issue.get("updateDate", ""),
                }
            )

        total = paging.get("total")
        if not isinstance(total, int):
            raise ValueError("SonarQube response is missing the total issue count")
        if page * page_size >= total:
            break
        page += 1

    write_csv(args.output, SONAR_FIELDS, issues)
    print(f"Exported {len(issues)} SonarQube issues to {args.output}")


def export_trivy(args: argparse.Namespace) -> None:
    with open(args.input, encoding="utf-8") as report_file:
        report = json.load(report_file)

    rows: list[dict[str, object]] = []
    for result in report.get("Results") or []:
        for vulnerability in result.get("Vulnerabilities") or []:
            rows.append(
                {
                    "commit": args.commit,
                    "image": args.image,
                    "target": result.get("Target", ""),
                    "class": result.get("Class", ""),
                    "type": result.get("Type", ""),
                    "vulnerabilityId": vulnerability.get("VulnerabilityID", ""),
                    "package": vulnerability.get("PkgName", ""),
                    "installedVersion": vulnerability.get("InstalledVersion", ""),
                    "fixedVersion": vulnerability.get("FixedVersion", ""),
                    "severity": vulnerability.get("Severity", ""),
                    "title": vulnerability.get("Title", ""),
                    "primaryUrl": vulnerability.get("PrimaryURL", ""),
                }
            )

    write_csv(args.output, TRIVY_FIELDS, rows)
    print(f"Exported {len(rows)} Trivy vulnerabilities to {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    sonar = commands.add_parser("sonar", help="Export SonarQube issues")
    sonar.add_argument("--host", required=True)
    sonar.add_argument("--project-key", required=True)
    sonar.add_argument("--commit", required=True)
    sonar.add_argument("--output", required=True)
    sonar.set_defaults(run=export_sonar)

    trivy = commands.add_parser("trivy", help="Convert a Trivy JSON report")
    trivy.add_argument("--input", required=True)
    trivy.add_argument("--commit", required=True)
    trivy.add_argument("--image", required=True)
    trivy.add_argument("--output", required=True)
    trivy.set_defaults(run=export_trivy)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        args.run(args)
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Report export failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
