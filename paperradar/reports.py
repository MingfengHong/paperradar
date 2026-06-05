from __future__ import annotations

import html
import re
from collections import defaultdict
from pathlib import Path

from .models import Recommendation, RunResult, Subscription, Topic, utc_now


MODULE_LABELS = {
    "paper_digest": "论文精选",
    "fresh_updates": "新文追踪",
    "periodic_review": "周期综述",
}


def generate_report(topic: Topic, subscription: Subscription, recommendations: list[Recommendation]) -> tuple[str, str]:
    modules = [module for module in subscription.report_modules if module in MODULE_LABELS]
    if not modules:
        modules = ["paper_digest"]
    lines = [
        f"# PaperRadar 报告：{topic.name}",
        "",
        f"- 订阅：{subscription.id}",
        f"- 生成时间：{utc_now()}",
        f"- 报告模块：{', '.join(MODULE_LABELS[module] for module in modules)}",
        "",
    ]
    if "paper_digest" in modules:
        lines.extend(render_paper_digest(recommendations, subscription.max_papers))
    if "fresh_updates" in modules:
        lines.extend(render_fresh_updates(recommendations))
    if "periodic_review" in modules:
        lines.extend(render_periodic_review(recommendations))
    markdown = "\n".join(lines).strip() + "\n"
    return markdown, markdown_to_html(markdown)


def render_paper_digest(recommendations: list[Recommendation], max_papers: int) -> list[str]:
    visible = [rec for rec in recommendations if not rec.filtered][:max_papers]
    filtered_count = len([rec for rec in recommendations if rec.filtered])
    lines = ["## 论文精选", ""]
    if not visible:
        lines.extend(["本次没有达到阈值的推荐论文。", ""])
    else:
        grouped = defaultdict(list)
        for rec in visible:
            grouped[rec.reading_action].append(rec)
        for action in ["精读", "略读", "收藏", "观察"]:
            if not grouped[action]:
                continue
            lines.extend([f"### {action}", ""])
            for rec in grouped[action]:
                lines.extend(render_recommendation(rec))
    lines.extend([f"过滤论文数量：{filtered_count}", ""])
    return lines


def render_fresh_updates(recommendations: list[Recommendation]) -> list[str]:
    fresh = [rec for rec in recommendations if rec.paper.source in {"arxiv", "journal_rss"} and not rec.filtered]
    lines = ["## 新文追踪", ""]
    if not fresh:
        lines.extend(["本次没有高相关新增论文。", ""])
        return lines
    by_source = defaultdict(list)
    for rec in fresh:
        label = "arXiv" if rec.paper.source == "arxiv" else rec.paper.extra.get("feed_title") or "期刊 RSS"
        by_source[label].append(rec)
    for source, items in by_source.items():
        lines.extend([f"### {source}", ""])
        for rec in items:
            lines.extend(render_recommendation(rec))
    return lines


def render_periodic_review(recommendations: list[Recommendation]) -> list[str]:
    visible = [rec for rec in recommendations if not rec.filtered]
    lines = ["## 周期综述", ""]
    if not visible:
        lines.extend(["本周期没有足够高相关论文，建议保留当前主题设置或适当放宽关键词。", ""])
        return lines
    top_terms = defaultdict(int)
    for rec in visible:
        for token in rec.paper.title.lower().split():
            token = token.strip(".,:;()[]{}")
            if len(token) > 4:
                top_terms[token] += 1
    terms = sorted(top_terms.items(), key=lambda item: item[1], reverse=True)[:8]
    themes = defaultdict(int)
    for rec in visible:
        if rec.classifier:
            themes[rec.classifier] += 1
    theme_text = ", ".join(f"{name}({count})" for name, count in sorted(themes.items(), key=lambda item: item[1], reverse=True)[:6])
    lines.extend(
        [
            f"- 高相关论文数量：{len(visible)}",
            f"- 建议优先精读：{len([rec for rec in visible if rec.reading_action == '精读'])} 篇",
            f"- 主要关键词：{', '.join(term for term, _ in terms) or '无明显聚类'}",
            f"- 主题分布：{theme_text or '无明显主题分布'}",
            "",
            "### 下一阶段阅读建议",
            "",
            "优先阅读“精读”和“略读”论文；对“观察”论文保持关注，不建议把低置信度条目放入主阅读计划。",
            "",
        ]
    )
    return lines


def render_recommendation(rec: Recommendation) -> list[str]:
    paper = rec.paper
    authors = ", ".join(paper.authors[:3])
    if len(paper.authors) > 3:
        authors += " et al."
    meta = " | ".join(part for part in [authors, str(paper.year or ""), paper.venue, paper.source] if part)
    lines = [
        f"- **{paper.title}**",
        f"  - {meta}" if meta else "  - metadata only",
        f"  - 建议：{rec.reading_action}；综合分：{rec.total_score():.2f}；置信度：{rec.confidence_score:.2f}；文献库相关性：{rec.library_similarity_score:.2f}",
        f"  - 理由：{rec.reason}",
    ]
    if rec.tldr:
        lines.append(f"  - TL;DR：{rec.tldr}")
    if rec.classifier or rec.keywords:
        detail = []
        if rec.classifier:
            detail.append(f"分类：{rec.classifier}")
        if rec.keywords:
            detail.append(f"关键词：{', '.join(rec.keywords[:6])}")
        lines.append(f"  - {'；'.join(detail)}")
    if rec.contribution:
        lines.append(f"  - 主要贡献：{rec.contribution}")
    if rec.limitation:
        lines.append(f"  - 阅读前注意：{rec.limitation}")
    if rec.related_library_items:
        lines.append(f"  - 与已有文献相关：{'; '.join(rec.related_library_items)}")
    if paper.doi:
        lines.append(f"  - DOI：{paper.doi}")
    if paper.arxiv_id:
        lines.append(f"  - arXiv：{paper.arxiv_id}")
    if paper.url:
        lines.append(f"  - 链接：{paper.url}")
    lines.append("")
    return lines


def markdown_to_html(markdown: str) -> str:
    body_lines: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("# "):
            body_lines.append(f"<h1>{inline_markdown(line[2:])}</h1>")
        elif line.startswith("## "):
            body_lines.append(f"<h2>{inline_markdown(line[3:])}</h2>")
        elif line.startswith("### "):
            body_lines.append(f"<h3>{inline_markdown(line[4:])}</h3>")
        elif line.startswith("- "):
            body_lines.append(f"<p class='bullet'>{inline_markdown(line[2:])}</p>")
        elif line.startswith("  - "):
            body_lines.append(f"<p class='subbullet'>{inline_markdown(line[4:])}</p>")
        elif not line.strip():
            body_lines.append("")
        else:
            body_lines.append(f"<p>{inline_markdown(line)}</p>")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PaperRadar Report</title>
  <style>
    body {{ margin: 0; background: #f6f6f4; color: #171717; font-family: Inter, system-ui, -apple-system, 'Segoe UI', sans-serif; }}
    main {{ max-width: 920px; margin: 0 auto; padding: 32px 20px 64px; }}
    h1, h2, h3 {{ letter-spacing: 0; }}
    h1 {{ font-size: 30px; }}
    h2 {{ margin-top: 34px; border-top: 1px solid #deded8; padding-top: 22px; }}
    p {{ line-height: 1.65; }}
    .bullet {{ margin-left: 0; }}
    .subbullet {{ margin-left: 20px; color: #444; }}
  </style>
</head>
<body><main>{''.join(body_lines)}</main></body></html>"""


def inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def save_report(result: RunResult, output_dir: Path) -> tuple[Path, Path]:
    reports_dir = output_dir / "reports"
    static_dir = output_dir / "static"
    site_dir = output_dir / "site"
    site_reports_dir = site_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    static_dir.mkdir(parents=True, exist_ok=True)
    site_reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = reports_dir / f"{result.run_id}.md"
    html_path = reports_dir / f"{result.run_id}.html"
    md_path.write_text(result.report_markdown, encoding="utf-8")
    html_path.write_text(result.report_html, encoding="utf-8")
    (static_dir / "index.html").write_text(result.report_html, encoding="utf-8")
    (site_reports_dir / f"{result.run_id}.md").write_text(result.report_markdown, encoding="utf-8")
    (site_reports_dir / f"{result.run_id}.html").write_text(result.report_html, encoding="utf-8")
    write_site_index(site_dir, site_reports_dir)
    return md_path, html_path


def public_report_url(public_base_url: str, run_id: str) -> str:
    base_url = str(public_base_url or "").strip().rstrip("/")
    if not base_url:
        return ""
    return f"{base_url}/reports/{run_id}.html"


def attach_public_report_link(markdown: str, html_doc: str, url: str) -> tuple[str, str]:
    if not url:
        return markdown, html_doc
    escaped_url = html.escape(url, quote=True)
    markdown_with_link = markdown.rstrip() + f"\n\n---\n\nFull report: {url}\n"
    link_html = f"<p><a href=\"{escaped_url}\">Full report</a></p>"
    if "</main>" in html_doc:
        html_with_link = html_doc.replace("</main>", f"{link_html}</main>", 1)
    else:
        html_with_link = html_doc + link_html
    return markdown_with_link, html_with_link


def write_site_index(site_dir: Path, reports_dir: Path) -> None:
    reports = sorted(reports_dir.glob("*.html"), key=lambda path: path.stat().st_mtime, reverse=True)
    items = "\n".join(
        f"<li><a href=\"reports/{html.escape(path.name, quote=True)}\">{html.escape(path.stem)}</a></li>"
        for path in reports
    )
    if not items:
        items = "<li>No reports generated yet.</li>"
    index_html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PaperRadar Reports</title>
  <style>
    body {{ margin: 0; background: #f6f6f4; color: #171717; font-family: Inter, system-ui, -apple-system, 'Segoe UI', sans-serif; }}
    main {{ max-width: 920px; margin: 0 auto; padding: 32px 20px 64px; }}
    li {{ margin: 10px 0; line-height: 1.5; }}
    a {{ color: #155f50; }}
  </style>
</head>
<body><main><h1>PaperRadar Reports</h1><ul>{items}</ul></main></body></html>"""
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(index_html, encoding="utf-8")
