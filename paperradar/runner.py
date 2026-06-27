from __future__ import annotations

import uuid
from pathlib import Path

from .config import AppConfig
from .library import load_library, load_zotero
from .llm import LLMClient
from .models import RunResult, Subscription, Topic
from .notifications import NotificationResult, send_report
from .ranking import prefilter_for_llm
from .recommender import recommend
from .reports import attach_public_report_link, generate_report, public_report_url, save_report
from .sources import fetch_arxiv, fetch_journal_rss, fetch_scholarly
from .storage import Storage


class Runner:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.config = AppConfig(self.root)
        self.storage = Storage(self.config.database_path)

    def close(self) -> None:
        self.storage.close()

    def run_subscription(self, subscription: Subscription, no_push: bool = False) -> RunResult:
        topic = self.config.topic_by_id(subscription.topic_id)
        if not topic:
            raise RuntimeError(f"Topic not found: {subscription.topic_id}")
        library_items = load_library(self.config.library, self.root)
        try:
            library_items.extend(load_zotero(self.config.zotero))
        except Exception:
            pass
        llm_client = LLMClient(self.config.settings.get("llm", {}))
        candidates = self.fetch_candidates(topic, subscription, llm_client)
        self.storage.save_papers(candidates)
        candidates = [paper for paper in candidates if not self.storage.already_pushed(subscription.id, paper)]
        prefiltered, library_signals = prefilter_for_llm(topic, subscription, candidates, library_items, self.config.settings.get("ranking", {}))
        recommendations = recommend(
            topic,
            subscription,
            prefiltered,
            library_items,
            llm_client,
            self.config.settings.get("ranking", {}),
            library_signals=library_signals,
        )
        report_markdown, report_html = generate_report(topic, subscription, recommendations)
        result = RunResult(
            run_id=f"{subscription.id}-{uuid.uuid4().hex[:8]}",
            topic=topic,
            subscription=subscription,
            candidates=candidates,
            recommendations=recommendations,
            report_markdown=report_markdown,
            report_html=report_html,
        )
        report_url = public_report_url(self.config.public_base_url, result.run_id)
        if report_url:
            result.report_markdown, result.report_html = attach_public_report_link(result.report_markdown, result.report_html, report_url)
        save_report(result, self.config.output_dir)
        self.storage.save_run(result)
        pushed = [rec.paper for rec in recommendations if not rec.filtered]
        if not no_push:
            delivery_results = send_report(self.config.notifications, subscription.channels, f"PaperRadar: {topic.name}", result.report_markdown, result.report_html)
            for delivery in delivery_results:
                print(f"notification {delivery.channel}: {delivery.message}")
            self._raise_for_delivery_failures(delivery_results)
            self.storage.mark_pushed(subscription.id, pushed)
        return result

    def _raise_for_delivery_failures(self, delivery_results: list[NotificationResult]) -> None:
        if not delivery_results:
            raise RuntimeError("no notification channels configured")
        failures = [result for result in delivery_results if not result.ok]
        if failures:
            details = "; ".join(f"{result.channel}: {result.message}" for result in failures)
            raise RuntimeError(f"notification failed: {details}")

    def run_all(self, no_push: bool = False, include_disabled: bool = False) -> list[RunResult]:
        results: list[RunResult] = []
        for subscription in self.config.subscriptions():
            if not subscription.enabled and not include_disabled:
                continue
            results.append(self.run_subscription(subscription, no_push=no_push))
        return results

    def fetch_candidates(self, topic: Topic, subscription: Subscription, llm_client: LLMClient):
        if subscription.type == "arxiv":
            multiplier = 12 if subscription.source.get("mode") == "daily_window" else 3
            return fetch_arxiv(subscription.source, topic.keywords, max_results=subscription.max_papers * multiplier)
        if subscription.type == "journal_rss":
            return fetch_journal_rss(subscription.source, max_results=subscription.max_papers * 3)
        if not llm_client.enabled:
            query = str(subscription.source.get("query") or "").strip() or " ".join([topic.research_question, " ".join(topic.keywords)]).strip()
            queries = [query] if query else []
        else:
            queries = llm_client.plan_scholarly_queries(topic, subscription)
        candidates = []
        for query in queries:
            source = dict(subscription.source)
            source["query"] = query
            fetched = fetch_scholarly(self.config.settings, source, max_results=subscription.max_papers * 3)
            for paper in fetched:
                paper.extra.setdefault("search_query", query)
            candidates.extend(fetched)
        deduped = {}
        for paper in candidates:
            deduped.setdefault(paper.normalized_key(), paper)
        return list(deduped.values())[: subscription.max_papers * max(3, len(queries))]
