"""Markdown run summary for the daily refresh (shown on the GitHub Actions run page via
$GITHUB_STEP_SUMMARY): source status, chunk changes, fact changes, gate problems."""

from app.ingest.facts import FACT_KEYS
from app.ingest.normalize import display_date


def facts_snapshot(docs: list[dict]) -> dict[str, dict[str, str]]:
    """{scheme: {fact: value}}: stored in each manifest so the next run can diff it."""
    return {
        doc["scheme"]: {k: doc["fields"][k]["value"] for k in FACT_KEYS if k in doc["fields"]}
        for doc in docs
    }


def diff_facts(old: dict, new: dict) -> list[tuple[str, str, str | None, str | None]]:
    changes = []
    for scheme in sorted(set(old) | set(new)):
        before, after = old.get(scheme, {}), new.get(scheme, {})
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                changes.append((scheme, key, before.get(key), after.get(key)))
    return changes


def _cell(value) -> str:
    return "—" if value in (None, "") else str(value).replace("|", "\\|")


def render(*, fetch_results=None, docs=None, stats=None, previous: dict | None = None,
           expected: dict | None = None, problems: list[str] | None = None,
           headline: str = "") -> str:
    lines = ["## Daily data refresh", ""]
    if headline:
        lines += [headline, ""]

    if fetch_results is not None:
        lines += ["### Sources", "", "| Source | Result |", "|---|---|"]
        for r in fetch_results:
            status = f"✅ fetched ({r.method})" if r.ok else f"❌ {_cell(r.error)}"
            lines.append(f"| `{r.source_id}` | {status} |")
        lines.append("")

    if docs:
        lines += ["### Page data dates", "", "| Scheme | Data as on |", "|---|---|"]
        for doc in docs:
            lines.append(f"| {doc['scheme_name']} | {display_date(doc.get('doc_date')) or '—'} |")
        lines.append("")

    if problems:
        lines += ["### ❌ Quality gate problems", ""] + [f"- {p}" for p in problems] + [""]

    if stats is not None:
        lines += ["### Index", ""]
        if stats.version is None:
            lines.append("No changes since the live version: nothing staged.")
        else:
            state = "activated" if stats.activated else "staged (not live yet)"
            lines.append(f"New version `{stats.version}` {state}; previous live version "
                         f"`{stats.previous_version or '—'}`.")
            lines.append("")
            lines.append(f"Chunks: {stats.added} added, {stats.updated} changed text, "
                         f"{stats.metadata_updated} metadata only, {stats.deleted} deleted, "
                         f"{stats.unchanged} unchanged; {stats.embedded} embedded.")
        lines.append("")

    if docs is not None:
        new_facts = facts_snapshot(docs)
        if previous is not None:
            changes = diff_facts(previous.get("facts") or {}, new_facts)
            lines += ["### Fact changes vs the live version", ""]
            if changes:
                lines += ["| Scheme | Fact | Before | After |", "|---|---|---|---|"]
                lines += [f"| {s} | {k} | {_cell(a)} | {_cell(b)} |" for s, k, a, b in changes]
            else:
                lines.append("None.")
            lines.append("")
        if expected:
            differs = [(s, k, v, new_facts.get(s, {}).get(k))
                       for s, fields in expected.items() if not s.startswith("_")
                       for k, v in fields.items() if new_facts.get(s, {}).get(k) != v]
            if differs:
                lines += ["### ⚠️ Differs from the hand-checked eval values",
                          "",
                          "Not blocking. Re-check the live page, then update "
                          "`backend/tests/eval/data/facts_expected.json` (docs/eval.md §9).",
                          "",
                          "| Scheme | Fact | Hand-checked | Now |", "|---|---|---|---|"]
                lines += [f"| {s} | {k} | {_cell(a)} | {_cell(b)} |" for s, k, a, b in differs]
                lines.append("")
    return "\n".join(lines) + "\n"
