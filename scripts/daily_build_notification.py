#!/usr/bin/env python3
"""Compose a Slack notification from trusted Actions run/job metadata."""
import argparse
import json
from pathlib import Path


def compose(run: dict, jobs: list[dict], channel: str) -> dict:
    if not channel:
        raise ValueError("SLACK_VULCAN_EVENT_CHANNEL_ID must be configured")
    if run.get("head_branch") != "daily" or run.get("status") != "completed":
        raise ValueError("Only completed daily builds can be reported")
    repository = run["repository"]["full_name"]
    base_url = f"https://github.com/{repository}/actions/runs/{run['id']}"
    conclusion = run["conclusion"]
    success = conclusion == "success"

    def escape(value):
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    title = "Model Compiler daily build succeeded" if success else f"Model Compiler daily build: {conclusion}"
    text = f"{title}\n<{base_url}|Build #{run['run_number']}> · commit {run['head_sha'][:12]} · attempt {run.get('run_attempt', 1)}"
    sections = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    if success:
        sections.append({"type": "section", "text": {"type": "mrkdwn", "text":
            "ARM64 and AMD64 packaging, installation, compilation tests, and publication completed. "
            f"<{base_url}#summary|Resolved component versions and build artifacts>"}})
    else:
        failed = [job for job in jobs if job.get("conclusion") not in (None, "success", "skipped", "neutral")]
        lines = [f"• {escape(job['name'][:160])}: {escape(job['conclusion'])}" for job in failed[:10]]
        sections.append({"type": "section", "text": {"type": "mrkdwn", "text":
            "*Failed or cancelled jobs*\n" + ("\n".join(lines) or "See the build log for details.")}})
    return {"channel": channel, "text": text, "blocks": sections,
            "unfurl_links": False, "unfurl_media": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    event = json.loads(args.event.read_text())
    pages = json.loads(args.jobs.read_text())
    jobs = [job for page in pages for job in page["jobs"]]
    args.output.write_text(json.dumps(compose(event["workflow_run"], jobs, args.channel)) + "\n")
