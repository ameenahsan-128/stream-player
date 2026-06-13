#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path

START_MARKER = "# stream-pipeline-start"
END_MARKER = "# stream-pipeline-end"
PIPELINE_ROOT = "/home/expertz/stream"
PIPELINE_SCRIPTS = ("match_scheduler.py", "precreate_posts.py")


def read_current_crontab():
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout
    if "no crontab" in (result.stderr or "").lower():
        return ""
    raise RuntimeError(result.stderr.strip() or "Unable to read current crontab.")


def strip_existing_pipeline(content):
    lines = content.splitlines()
    cleaned = []
    in_block = False

    for line in lines:
        stripped = line.strip()
        if stripped == START_MARKER:
            in_block = True
            continue
        if stripped == END_MARKER:
            in_block = False
            continue
        if in_block:
            continue
        if PIPELINE_ROOT in line and any(script in line for script in PIPELINE_SCRIPTS):
            continue
        cleaned.append(line)

    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned)


def build_crontab(existing, template):
    existing = strip_existing_pipeline(existing)
    template = template.strip()
    parts = [part for part in (existing, template) if part]
    return "\n\n".join(parts) + "\n"


def install_crontab(content):
    result = subprocess.run(["crontab", "-"], input=content, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to install crontab.")


def main():
    parser = argparse.ArgumentParser(description="Install the stream pipeline cron block without disturbing unrelated jobs.")
    parser.add_argument("--template", default="crontab.stream_pipeline", help="Cron template file to install.")
    parser.add_argument("--apply", action="store_true", help="Install the generated crontab. Default is dry-run.")
    args = parser.parse_args()

    template_path = Path(args.template)
    if not template_path.exists():
        print(f"[-] Cron template not found: {template_path}")
        sys.exit(1)

    try:
        current = read_current_crontab()
        next_crontab = build_crontab(current, template_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[-] {exc}")
        sys.exit(1)

    if not args.apply:
        print(next_crontab, end="")
        print("\n[dry-run] Crontab was not changed.")
        return

    try:
        install_crontab(next_crontab)
    except Exception as exc:
        print(f"[-] {exc}")
        sys.exit(1)

    print("[+] Stream pipeline cron block installed.")


if __name__ == "__main__":
    main()
