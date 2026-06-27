from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from pathlib import Path

import yaml

from .config import AppConfig, init_project, read_yaml
from .notifications import test_channel
from .runner import Runner
from .sources.rss import discover_feed_candidates
from .web.server import run_web


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="paperradar")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create config templates and data/output directories")
    setup = sub.add_parser("setup", help="Interactive first-run setup wizard")
    setup.add_argument("--non-interactive", action="store_true", help="Write a usable default config from arguments without prompts")
    setup.add_argument("--topic-name", default="")
    setup.add_argument("--question", default="")
    setup.add_argument("--keywords", default="")
    setup.add_argument("--channel", choices=["none", "email", "feishu"], default="")
    setup.add_argument("--email-from", default="")
    setup.add_argument("--email-to", default="")
    setup.add_argument("--email-password", default="")
    setup.add_argument("--email-smtp-server", default="")
    setup.add_argument("--email-smtp-port", default="")
    setup.add_argument("--feishu-webhook-url", default="")
    setup.add_argument("--llm-base-url", default="")
    setup.add_argument("--llm-model", default="")
    setup.add_argument("--llm-api-key", default="")
    setup.add_argument("--openalex-api-key", default="")
    setup.add_argument("--enable-arxiv", action="store_true")
    setup.add_argument("--arxiv-categories", default="cs.AI,cs.CL")
    sub.add_parser("doctor", help="Check configuration")

    topic = sub.add_parser("topic")
    topic_sub = topic.add_subparsers(dest="topic_command", required=True)
    topic_create = topic_sub.add_parser("create")
    topic_create.add_argument("--id")
    topic_create.add_argument("--name", required=True)
    topic_create.add_argument("--question", default="")
    topic_create.add_argument("--keywords", default="", help="Comma separated keywords")
    topic_sub.add_parser("list")

    paper_sub = sub.add_parser("paper-subscription")
    paper_sub_sub = paper_sub.add_subparsers(dest="subscription_command", required=True)
    paper_create = paper_sub_sub.add_parser("create")
    add_common_subscription_args(paper_create)
    paper_create.add_argument("--query", required=True)

    arxiv_sub = sub.add_parser("arxiv-subscription")
    arxiv_sub_sub = arxiv_sub.add_subparsers(dest="subscription_command", required=True)
    arxiv_create = arxiv_sub_sub.add_parser("create")
    add_common_subscription_args(arxiv_create)
    arxiv_create.add_argument("--categories", default="cs.AI", help="Comma separated arXiv categories")
    arxiv_create.add_argument("--query", default="")
    arxiv_create.add_argument("--mode", default="daily_window", choices=["daily_window", "latest", "category_latest"])

    journal_sub = sub.add_parser("journal-subscription")
    journal_sub_sub = journal_sub.add_subparsers(dest="subscription_command", required=True)
    journal_create = journal_sub_sub.add_parser("create")
    add_common_subscription_args(journal_create)
    journal_create.add_argument("--feed-url", action="append", default=[])
    journal_create.add_argument("--journal", default="")

    report = sub.add_parser("report")
    report_sub = report.add_subparsers(dest="report_command", required=True)
    report_sub.add_parser("list")

    run_parser = sub.add_parser("run", help="Run enabled subscriptions")
    run_parser.add_argument("--subscription", "-s", help="Run one subscription id")
    run_parser.add_argument("--no-push", action="store_true", help="Generate report without sending notifications")
    run_parser.add_argument("--include-disabled", action="store_true", help="Allow disabled subscriptions to run")

    schedule = sub.add_parser("schedule", help="Scheduler commands")
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)
    schedule_sub.add_parser("run-once")
    daemon = schedule_sub.add_parser("daemon")
    daemon.add_argument("--interval", type=int, default=1800, help="Loop interval in seconds")

    test = sub.add_parser("test-notification")
    test.add_argument("channel")

    discover = sub.add_parser("journal-rss-discover")
    discover.add_argument("--journal", default="")
    discover.add_argument("--homepage-url", default="")

    web = sub.add_parser("web")
    web.add_argument("--host")
    web.add_argument("--port", type=int)

    deploy = sub.add_parser("deploy")
    deploy_sub = deploy.add_subparsers(dest="deploy_command", required=True)
    deploy_sub.add_parser("github-actions-template")
    deploy_sub.add_parser("docker-compose-template")
    deploy_sub.add_parser("systemd-template")

    args = parser.parse_args(argv)
    root = Path.cwd()

    if args.command == "init":
        created = init_project(root)
        print("PaperRadar initialized.")
        for path in created:
            print(f"created: {path.relative_to(root)}")
        return

    if args.command == "setup":
        setup_command(root, args)
        return

    if args.command == "doctor":
        doctor(root)
        return

    if args.command == "topic":
        if args.topic_command == "create":
            topic_create_command(root, args)
        elif args.topic_command == "list":
            for topic_item in AppConfig(root).topics():
                print(f"{topic_item.id}\t{topic_item.name}\t{topic_item.status}")
        return

    if args.command in {"paper-subscription", "arxiv-subscription", "journal-subscription"}:
        if args.subscription_command == "create":
            subscription_create_command(root, args.command, args)
        return

    if args.command == "report":
        if args.report_command == "list":
            runner = Runner(root)
            try:
                for row in runner.storage.recent_runs(50):
                    print(f"{row['created_at']}\t{row['id']}\t{row['subscription_id']}\t{row['candidate_count']}")
            finally:
                runner.close()
        return

    if args.command == "run":
        runner = Runner(root)
        try:
            subscriptions = runner.config.subscriptions()
            if args.subscription:
                subscriptions = [sub for sub in subscriptions if sub.id == args.subscription]
                if not subscriptions:
                    raise SystemExit(f"subscription not found: {args.subscription}")
                try:
                    result = runner.run_subscription(subscriptions[0], no_push=args.no_push)
                except RuntimeError as exc:
                    raise SystemExit(f"ERROR: {exc}") from exc
                print_result(result)
            else:
                try:
                    results = runner.run_all(no_push=args.no_push, include_disabled=args.include_disabled)
                except RuntimeError as exc:
                    raise SystemExit(f"ERROR: {exc}") from exc
                for result in results:
                    print_result(result)
        finally:
            runner.close()
        return

    if args.command == "schedule":
        if args.schedule_command == "run-once":
            runner = Runner(root)
            try:
                try:
                    results = runner.run_all(no_push=False)
                except RuntimeError as exc:
                    raise SystemExit(f"ERROR: {exc}") from exc
                for result in results:
                    print_result(result)
            finally:
                runner.close()
            return
        if args.schedule_command == "daemon":
            while True:
                runner = Runner(root)
                try:
                    try:
                        results = runner.run_all(no_push=False)
                    except RuntimeError as exc:
                        print(f"ERROR: {exc}")
                        results = []
                    for result in results:
                        print_result(result)
                finally:
                    runner.close()
                time.sleep(args.interval)

    if args.command == "test-notification":
        config = AppConfig(root)
        result = test_channel(config.notifications, args.channel)
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result.ok else 1)

    if args.command == "journal-rss-discover":
        candidates = discover_feed_candidates(args.journal, args.homepage_url)
        print(json.dumps([candidate.__dict__ for candidate in candidates], ensure_ascii=False, indent=2))
        return

    if args.command == "web":
        config = AppConfig(root)
        app_cfg = config.settings.get("app", {})
        run_web(root, host=args.host or app_cfg.get("web_host", "127.0.0.1"), port=args.port or int(app_cfg.get("web_port", 8766)))
        return

    if args.command == "deploy":
        print_template(args.deploy_command)
        return


def doctor(root: Path) -> None:
    config = AppConfig(root)
    checks = []
    checks.append(("config/settings.yaml", (root / "config" / "settings.yaml").exists()))
    checks.append(("topics", bool(config.topics())))
    checks.append(("subscriptions", bool(config.subscriptions())))
    channels = config.notifications.get("channels", {})
    checks.append(("notification channel configured", has_ready_notification_channel(channels)))
    checks.append(("llm configured", bool(config.settings.get("llm", {}).get("enabled") and config.settings.get("llm", {}).get("api_key"))))
    checks.append(("database parent writable", config.database_path.parent.exists() or can_create(config.database_path.parent)))
    for name, ok in checks:
        print(f"{'OK' if ok else 'WARN'}  {name}")


def can_create(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


def has_ready_notification_channel(channels: dict) -> bool:
    required_fields = {
        "feishu": ["webhook_url"],
        "dingtalk": ["webhook_url"],
        "wework": ["webhook_url"],
        "generic": ["webhook_url"],
        "slack": ["webhook_url"],
        "bark": ["url"],
        "telegram": ["bot_token", "chat_id"],
        "ntfy": ["topic"],
        "email": ["from", "password", "to"],
    }
    for channel, cfg in channels.items():
        if not cfg.get("enabled"):
            continue
        required = required_fields.get(channel, [])
        if all(str(cfg.get(field) or "").strip() for field in required):
            return True
    return False


def print_result(result) -> None:
    print(f"run_id={result.run_id} candidates={len(result.candidates)} recommendations={len([r for r in result.recommendations if not r.filtered])}")


def setup_command(root: Path, args: argparse.Namespace) -> None:
    created = init_project(root)
    answers = setup_answers(args)
    apply_quickstart_config(root, answers)
    print("PaperRadar setup completed.")
    if created:
        print("initialized:")
        for path in created:
            print(f"  - {path.relative_to(root)}")
    print("")
    print("Next steps:")
    print("  1. paperradar doctor")
    print("  2. paperradar run --no-push")
    if answers["channel"] != "none":
        print(f"  3. paperradar test-notification {answers['channel']}")
        print("  4. paperradar run")
    else:
        print("  3. Configure a notification channel before running without --no-push.")
    print("")
    print("For GitHub Actions, copy the values from .env into repository Secrets with the same names.")


def setup_answers(args: argparse.Namespace) -> dict[str, str | bool]:
    if args.non_interactive:
        return {
            "topic_name": args.topic_name or "My Research Topic",
            "question": args.question or "What papers are worth reading for my current research topic?",
            "keywords": args.keywords or "large language model,scientific literature,recommendation",
            "channel": args.channel or "none",
            "email_from": args.email_from,
            "email_to": args.email_to,
            "email_password": args.email_password,
            "email_smtp_server": args.email_smtp_server,
            "email_smtp_port": args.email_smtp_port,
            "feishu_webhook_url": args.feishu_webhook_url,
            "llm_api_key": args.llm_api_key,
            "llm_base_url": args.llm_base_url,
            "llm_model": args.llm_model,
            "openalex_api_key": args.openalex_api_key,
            "enable_arxiv": args.enable_arxiv,
            "arxiv_categories": args.arxiv_categories,
        }

    print("PaperRadar first-run setup")
    print("Press Enter to keep the default shown in brackets.")
    topic_name = prompt("Research topic name", args.topic_name or "My Research Topic")
    question = prompt("Research question / reading goal", args.question or "What papers are worth reading for my current research topic?")
    keywords = prompt("Keywords, comma separated", args.keywords or "large language model,scientific literature,recommendation")
    enable_arxiv = prompt_yes_no("Enable arXiv daily tracking", args.enable_arxiv)
    arxiv_categories = prompt("arXiv categories", args.arxiv_categories or "cs.AI,cs.CL") if enable_arxiv else args.arxiv_categories
    channel = prompt_choice("Notification channel", ["email", "feishu", "none"], args.channel or "email")

    email_from = email_to = email_password = email_smtp_server = email_smtp_port = ""
    feishu_webhook_url = ""
    if channel == "email":
        email_from = prompt("EMAIL_FROM", args.email_from)
        email_to = prompt("EMAIL_TO", args.email_to or email_from)
        email_password = prompt_secret("EMAIL_PASSWORD / SMTP password")
        email_smtp_server = prompt("EMAIL_SMTP_SERVER, optional", args.email_smtp_server)
        email_smtp_port = prompt("EMAIL_SMTP_PORT, optional", args.email_smtp_port)
    elif channel == "feishu":
        feishu_webhook_url = prompt_secret("FEISHU_WEBHOOK_URL")

    llm_api_key = prompt_secret("LLM_API_KEY, optional")
    llm_base_url = prompt("LLM_BASE_URL", args.llm_base_url or "https://api.openai.com/v1") if llm_api_key else args.llm_base_url
    llm_model = prompt("LLM_MODEL", args.llm_model or "gpt-4o-mini") if llm_api_key else args.llm_model
    openalex_api_key = prompt_secret("OPENALEX_API_KEY, optional")

    return {
        "topic_name": topic_name,
        "question": question,
        "keywords": keywords,
        "channel": channel,
        "email_from": email_from,
        "email_to": email_to,
        "email_password": email_password,
        "email_smtp_server": email_smtp_server,
        "email_smtp_port": email_smtp_port,
        "feishu_webhook_url": feishu_webhook_url,
        "llm_api_key": llm_api_key,
        "llm_base_url": llm_base_url,
        "llm_model": llm_model,
        "openalex_api_key": openalex_api_key,
        "enable_arxiv": enable_arxiv,
        "arxiv_categories": arxiv_categories,
    }


def apply_quickstart_config(root: Path, answers: dict[str, str | bool]) -> None:
    topic_id = "default"
    channel = str(answers["channel"])
    channels = [] if channel == "none" else [channel]
    topic = {
        "id": topic_id,
        "name": str(answers["topic_name"]).strip() or "My Research Topic",
        "research_question": str(answers["question"]).strip(),
        "keywords": split_csv(str(answers["keywords"])),
        "exclude_keywords": [],
        "venues": [],
        "reading_goal": str(answers["question"]).strip() or "Find papers worth reading without being overwhelmed",
        "status": "active",
        "library_tags": [],
    }
    write_yaml(root / "config" / "topics.yaml", {"topics": [topic]})

    subscriptions = [
        {
            "id": "daily-paper-digest",
            "topic_id": topic_id,
            "type": "paper",
            "enabled": True,
            "report_modules": ["paper_digest", "periodic_review"],
            "schedule": "manual",
            "max_papers": 8,
            "min_score": 0.55,
            "channels": channels,
            "source": {"query": ""},
        },
        {
            "id": "arxiv-daily",
            "topic_id": topic_id,
            "type": "arxiv",
            "enabled": bool(answers["enable_arxiv"]),
            "report_modules": ["fresh_updates"],
            "schedule": "manual",
            "max_papers": 8,
            "min_score": 0.55,
            "channels": channels,
            "source": {"categories": split_csv(str(answers["arxiv_categories"])) or ["cs.AI", "cs.CL"], "query": "", "mode": "daily_window"},
        },
    ]
    write_yaml(root / "config" / "subscriptions.yaml", {"subscriptions": subscriptions})

    notifications = read_yaml(root / "config" / "notifications.yaml", {"channels": {}})
    for cfg in notifications.get("channels", {}).values():
        cfg["enabled"] = False
    if channel != "none":
        notifications.setdefault("channels", {}).setdefault(channel, {})["enabled"] = True
    write_yaml(root / "config" / "notifications.yaml", notifications)

    env_values = {
        "LLM_API_KEY": str(answers["llm_api_key"]),
        "LLM_BASE_URL": str(answers["llm_base_url"]),
        "LLM_MODEL": str(answers["llm_model"]),
        "OPENALEX_API_KEY": str(answers["openalex_api_key"]),
        "FEISHU_WEBHOOK_URL": str(answers["feishu_webhook_url"]),
        "EMAIL_FROM": str(answers["email_from"]),
        "EMAIL_PASSWORD": str(answers["email_password"]),
        "EMAIL_TO": str(answers["email_to"]),
        "EMAIL_SMTP_SERVER": str(answers["email_smtp_server"]),
        "EMAIL_SMTP_PORT": str(answers["email_smtp_port"]),
    }
    update_env_file(root / ".env", {key: value for key, value in env_values.items() if value})


def prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def prompt_secret(label: str) -> str:
    return getpass.getpass(f"{label}: ").strip()


def prompt_yes_no(label: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    value = input(f"{label} [{hint}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true", "on"}


def prompt_choice(label: str, choices: list[str], default: str) -> str:
    choice_text = "/".join(choices)
    while True:
        value = prompt(f"{label} ({choice_text})", default).lower()
        if value in choices:
            return value
        print(f"Please choose one of: {choice_text}")


def update_env_file(path: Path, values: dict[str, str]) -> None:
    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            existing[key] = value
    existing.update(values)
    ordered_keys = [
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "OPENALEX_API_KEY",
        "EMBEDDING_API_KEY",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_MODEL",
        "FEISHU_WEBHOOK_URL",
        "DINGTALK_WEBHOOK_URL",
        "WEWORK_WEBHOOK_URL",
        "GENERIC_WEBHOOK_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "NTFY_SERVER_URL",
        "NTFY_TOPIC",
        "NTFY_TOKEN",
        "BARK_URL",
        "SLACK_WEBHOOK_URL",
        "EMAIL_FROM",
        "EMAIL_PASSWORD",
        "EMAIL_TO",
        "EMAIL_SMTP_SERVER",
        "EMAIL_SMTP_PORT",
        "ZOTERO_USER_ID",
        "ZOTERO_GROUP_ID",
        "ZOTERO_API_KEY",
        "PAPERRADAR_PUBLIC_BASE_URL",
    ]
    lines = [f"{key}={existing.get(key, '')}" for key in ordered_keys if key in existing or key in values]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def print_template(kind: str) -> None:
    if kind == "github-actions-template":
        print(GITHUB_ACTIONS_TEMPLATE)
    elif kind == "docker-compose-template":
        print(DOCKER_COMPOSE_TEMPLATE)
    elif kind == "systemd-template":
        print(SYSTEMD_TEMPLATE)


GITHUB_ACTIONS_TEMPLATE = """name: PaperRadar

on:
  push:
    branches: ["main"]
    paths:
      - ".github/workflows/paperradar.yml"
      - "config/**"
      - "paperradar/**"
      - "tests/**"
      - "pyproject.toml"
  workflow_dispatch:
    inputs:
      no_push:
        description: "Generate reports without sending notifications"
        required: false
        default: "false"
      deploy_pages:
        description: "Deploy output/site to GitHub Pages"
        required: false
        default: "false"
      subscription:
        description: "Optional subscription id to run, for example daily-paper-digest"
        required: false
        default: ""
  schedule:
    - cron: "0 23 * * *"

concurrency:
  group: paperradar-${{ github.ref_name }}
  cancel-in-progress: true

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 45
    permissions:
      contents: read
    env:
      NO_PUSH: ${{ github.event_name == 'workflow_dispatch' && inputs.no_push || github.event_name == 'push' && 'true' || 'false' }}
      PAPERRADAR_SUBSCRIPTION: ${{ github.event_name == 'workflow_dispatch' && inputs.subscription || '' }}
      PAPERRADAR_PUBLIC_BASE_URL_OVERRIDE: ${{ vars.PAPERRADAR_PUBLIC_BASE_URL }}
      LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
      LLM_BASE_URL: ${{ secrets.LLM_BASE_URL }}
      LLM_MODEL: ${{ secrets.LLM_MODEL }}
      OPENALEX_API_KEY: ${{ secrets.OPENALEX_API_KEY }}
      EMBEDDING_API_KEY: ${{ secrets.EMBEDDING_API_KEY }}
      EMBEDDING_BASE_URL: ${{ secrets.EMBEDDING_BASE_URL }}
      EMBEDDING_MODEL: ${{ secrets.EMBEDDING_MODEL }}
      FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}
      DINGTALK_WEBHOOK_URL: ${{ secrets.DINGTALK_WEBHOOK_URL }}
      WEWORK_WEBHOOK_URL: ${{ secrets.WEWORK_WEBHOOK_URL }}
      GENERIC_WEBHOOK_URL: ${{ secrets.GENERIC_WEBHOOK_URL }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
      NTFY_SERVER_URL: ${{ secrets.NTFY_SERVER_URL }}
      NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
      NTFY_TOKEN: ${{ secrets.NTFY_TOKEN }}
      BARK_URL: ${{ secrets.BARK_URL }}
      SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
      EMAIL_FROM: ${{ secrets.EMAIL_FROM }}
      EMAIL_PASSWORD: ${{ secrets.EMAIL_PASSWORD }}
      EMAIL_TO: ${{ secrets.EMAIL_TO }}
      EMAIL_SMTP_SERVER: ${{ secrets.EMAIL_SMTP_SERVER }}
      EMAIL_SMTP_PORT: ${{ secrets.EMAIL_SMTP_PORT }}
      ZOTERO_USER_ID: ${{ secrets.ZOTERO_USER_ID }}
      ZOTERO_GROUP_ID: ${{ secrets.ZOTERO_GROUP_ID }}
      ZOTERO_API_KEY: ${{ secrets.ZOTERO_API_KEY }}
    steps:
      - uses: actions/checkout@v4
      - name: Configure public report URL
        run: |
          if [ -n "$PAPERRADAR_PUBLIC_BASE_URL_OVERRIDE" ]; then
            echo "PAPERRADAR_PUBLIC_BASE_URL=$PAPERRADAR_PUBLIC_BASE_URL_OVERRIDE" >> "$GITHUB_ENV"
          elif [ "${PAPERRADAR_DEPLOY_PAGES:-}" = "true" ]; then
            echo "PAPERRADAR_PUBLIC_BASE_URL=https://${GITHUB_REPOSITORY_OWNER}.github.io/${GITHUB_REPOSITORY#*/}" >> "$GITHUB_ENV"
          else
            echo "PAPERRADAR_PUBLIC_BASE_URL=" >> "$GITHUB_ENV"
          fi
        env:
          PAPERRADAR_DEPLOY_PAGES: ${{ vars.PAPERRADAR_DEPLOY_PAGES }}
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install
        run: python -m pip install -e . pytest
      - name: Compile
        run: python -m compileall -q paperradar
      - name: Test
        run: python -m pytest -q
      - name: Doctor
        run: paperradar doctor
      - name: Run
        run: |
          if [ -n "$PAPERRADAR_SUBSCRIPTION" ]; then
            TARGET_ARGS="--subscription $PAPERRADAR_SUBSCRIPTION"
          else
            TARGET_ARGS=""
          fi
          if [ "$NO_PUSH" = "true" ]; then
            paperradar run $TARGET_ARGS --no-push
          else
            paperradar run $TARGET_ARGS
          fi
      - name: Upload reports
        uses: actions/upload-artifact@v4
        with:
          name: paperradar-output
          path: output/

  deploy-pages:
    needs: run
    if: ${{ (github.event_name == 'workflow_dispatch' && inputs.deploy_pages == 'true') || (github.event_name != 'workflow_dispatch' && vars.PAPERRADAR_DEPLOY_PAGES == 'true') }}
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      contents: read
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Download reports
        uses: actions/download-artifact@v4
        with:
          name: paperradar-output
          path: output
      - uses: actions/configure-pages@v5
      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: output/site
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
"""

DOCKER_COMPOSE_TEMPLATE = """services:
  paperradar:
    build: .
    working_dir: /app
    command: paperradar schedule daemon --interval 1800
    volumes:
      - ./config:/app/config
      - ./data:/app/data
      - ./output:/app/output
    env_file:
      - .env
"""

SYSTEMD_TEMPLATE = """[Unit]
Description=PaperRadar scheduled runner
After=network-online.target

[Service]
WorkingDirectory=/opt/paperradar
EnvironmentFile=/opt/paperradar/.env
ExecStart=/opt/paperradar/.venv/bin/paperradar schedule daemon --interval 1800
Restart=always

[Install]
WantedBy=multi-user.target
"""


def add_common_subscription_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id")
    parser.add_argument("--topic-id", default="default")
    parser.add_argument("--modules", default="paper_digest", help="Comma separated: paper_digest,fresh_updates,periodic_review")
    parser.add_argument("--max-papers", type=int, default=8)
    parser.add_argument("--min-score", type=float, default=0.55)
    parser.add_argument("--channels", default="", help="Comma separated notification channels")
    parser.add_argument("--enabled", action="store_true", default=True)


def topic_create_command(root: Path, args: argparse.Namespace) -> None:
    path = root / "config" / "topics.yaml"
    data = read_yaml(path, {"topics": []})
    topics = data.setdefault("topics", [])
    topic_id = args.id or slug_from(args.name)
    topics[:] = [topic for topic in topics if topic.get("id") != topic_id]
    topics.append(
        {
            "id": topic_id,
            "name": args.name,
            "research_question": args.question,
            "keywords": split_csv(args.keywords),
            "exclude_keywords": [],
            "venues": [],
            "reading_goal": "Find papers worth reading without being overwhelmed",
            "status": "active",
        }
    )
    write_yaml(path, data)
    print(f"created topic: {topic_id}")


def subscription_create_command(root: Path, command: str, args: argparse.Namespace) -> None:
    path = root / "config" / "subscriptions.yaml"
    data = read_yaml(path, {"subscriptions": []})
    subscriptions = data.setdefault("subscriptions", [])
    if command == "paper-subscription":
        sub_type = "paper"
        source = {"query": args.query}
        default_id = f"{args.topic_id}-paper"
    elif command == "arxiv-subscription":
        sub_type = "arxiv"
        source = {"categories": split_csv(args.categories), "query": args.query, "mode": args.mode}
        default_id = f"{args.topic_id}-arxiv"
    else:
        sub_type = "journal_rss"
        feeds = [{"name": args.journal or "Journal RSS", "url": url, "journal": args.journal} for url in args.feed_url]
        source = {"feeds": feeds}
        default_id = f"{args.topic_id}-journal-rss"
    sid = args.id or slug_from(default_id)
    subscriptions[:] = [item for item in subscriptions if item.get("id") != sid]
    subscriptions.append(
        {
            "id": sid,
            "topic_id": args.topic_id,
            "type": sub_type,
            "enabled": args.enabled,
            "report_modules": split_csv(args.modules) or ["paper_digest"],
            "schedule": "manual",
            "max_papers": args.max_papers,
            "min_score": args.min_score,
            "channels": split_csv(args.channels),
            "source": source,
        }
    )
    write_yaml(path, data)
    print(f"created subscription: {sid}")


def split_csv(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def slug_from(value: str) -> str:
    return "-".join(part for part in "".join(ch.lower() if ch.isalnum() else "-" for ch in value).split("-") if part)


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)


if __name__ == "__main__":
    main(sys.argv[1:])
