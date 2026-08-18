#!/usr/bin/env python3
"""Collect and benchmark the CMS template corpus against the live annotator.

This is intentionally outside the default test suite: ``run`` makes paid
OpenAI requests. Credentials are read into process memory and are never written
to the corpus or result files.

Running this sends client template content to a third-party API. Choose the
corpus deliberately and obtain the appropriate authorization before proceeding.
Annotation output can vary by a few fields between identical runs at the
model's default temperature, so repeat apparent regressions instead of treating
a single before/after sample as proof. This limitation applies to model-choice
benchmarks as well as code-change benchmarks.

Examples:
  uv run python scripts/run_annotation_corpus.py collect --output /tmp/corpus.json
  uv run python scripts/run_annotation_corpus.py run \
      --corpus /tmp/corpus.json --output /tmp/luna.json \
      --model gpt-5.6-luna --effort medium
  uv run python scripts/run_annotation_corpus.py run \
      --corpus /tmp/corpus.json --output /tmp/mini-failures.json \
      --model gpt-4o-mini --failures-from /tmp/luna.json
  uv run python scripts/run_annotation_corpus.py replay \
      --corpus /tmp/corpus.json --results /tmp/luna.json \
      --tree /tmp/cms-pre36 --output /tmp/pre36.json
"""

from __future__ import annotations

import argparse
import contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import requests
from bs4 import BeautifulSoup
from dotenv import dotenv_values

WORKSPACE = Path(__file__).resolve().parents[1]
MCP_CONFIG = Path("/home/bernardjr/Desktop/Code/work/katalyst-ai/.mcp.json")
PASSBOLT_DIR = Path("/home/bernardjr/Desktop/Code/work/katalyst-ai/.secrets")
PASSBOLT_URL = "https://passbolt.katek.app"
PROD_RESOURCE_ID = "6cb51404-c059-4f4a-993c-74389f5ce8ec"
STAGING_COMPOSE_ID = "Inld3aqaYsIyeuapZWpjh"
PRICES = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-5.6-luna": {"input": 0.20, "output": 1.20},
}
ANNOTATION_ATTR_RE = re.compile(
    r"\sdata-(?:section|edit|type|label|group|icon|tokens|cms-ref)\s*=\s*"
    r'(?:"[^"]*"|\'[^\']*\')',
    re.IGNORECASE,
)

FAILED_SMOKE_HTML = """<!doctype html>
<html lang="en">
  <head><title>Production annotation smoke</title></head>
  <body>
    <main>
      <section class="consultation-intro">
        <h1>Thoughtful care for a healthier tomorrow</h1>
        <p>Book a private consultation with our experienced clinical team.</p>
        <img src="https://example.com/consultation-room.jpg"
             alt="A welcoming medical consultation room"
             width="900" height="600">
      </section>
    </main>
  </body>
</html>"""


def json_write(path: Path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n")


def load_dokploy_env():
    config = json.loads(MCP_CONFIG.read_text())
    dokploy = config["mcpServers"]["dokploy-katek"]["env"]
    response = httpx.get(
        dokploy["DOKPLOY_URL"].rstrip("/") + "/api/compose.one",
        params={"composeId": STAGING_COMPOSE_ID},
        headers={"x-api-key": dokploy["DOKPLOY_API_KEY"]},
        timeout=30,
    )
    response.raise_for_status()
    return dotenv_values(stream=io.StringIO(response.json().get("env", "")))


def gpg_decrypt(armored):
    result = subprocess.run(
        ["gpg", "--batch", "--yes", "--decrypt"],
        input=armored,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def passbolt_api(path):
    result = subprocess.run(
        [
            "curl",
            "-sSg",
            "-b",
            str(PASSBOLT_DIR / "cookies.txt"),
            f"{PASSBOLT_URL}{path}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    if "body" not in payload:
        raise RuntimeError("Passbolt session is unavailable")
    return payload["body"]


def production_credential():
    subprocess.run(
        [str(PASSBOLT_DIR / "passbolt-bot-login.sh")],
        cwd=PASSBOLT_DIR,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    metadata_key = passbolt_api(
        "/metadata/keys.json?api-version=v2&contain[metadata_private_keys]=1"
    )[0]
    private_key = json.loads(
        gpg_decrypt(metadata_key["metadata_private_keys"][0]["data"])
    )
    subprocess.run(
        ["gpg", "--batch", "--yes", "--import"],
        input=private_key["armored_key"],
        capture_output=True,
        text=True,
        check=True,
    )
    resource = passbolt_api(
        f"/resources/{PROD_RESOURCE_ID}.json?api-version=v2&contain[secret]=1"
    )
    metadata = json.loads(gpg_decrypt(resource["metadata"]))
    secret = json.loads(gpg_decrypt(resource["secrets"][0]["data"]))
    username = metadata.get("username") or ""
    password = secret.get("password") or ""
    if not username or not password:
        raise RuntimeError("Production Passbolt resource is incomplete")
    return username, password


def login(base_url, username, password):
    session = requests.Session()
    page = session.get(f"{base_url}/login/", timeout=30)
    page.raise_for_status()
    soup = BeautifulSoup(page.text, "html.parser")
    csrf = soup.select_one('input[name="csrfmiddlewaretoken"]')
    if csrf is None:
        raise RuntimeError(f"{base_url}: login CSRF token missing")
    response = session.post(
        f"{base_url}/login/",
        data={
            "csrfmiddlewaretoken": csrf.get("value", ""),
            "username": username,
            "password": password,
        },
        headers={"Referer": f"{base_url}/login/"},
        allow_redirects=True,
        timeout=30,
    )
    if response.status_code != 200 or response.url.rstrip("/").endswith("/login"):
        raise RuntimeError(f"{base_url}: dashboard login failed")
    return session


def dashboard_templates(base_url, session, environment):
    response = session.get(f"{base_url}/dashboard/templates/", timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    paths = sorted(
        {
            link.get("href")
            for link in soup.select("a[href]")
            if re.fullmatch(r"/dashboard/templates/\d+/", link.get("href", ""))
        },
        key=lambda value: int(value.rstrip("/").rsplit("/", 1)[1]),
    )
    print(
        json.dumps(
            {"collecting": environment, "template_detail_pages": len(paths)}
        ),
        flush=True,
    )

    def fetch(path):
        detail = requests.get(
            f"{base_url}{path}", cookies=session.cookies, timeout=30
        )
        detail.raise_for_status()
        detail_soup = BeautifulSoup(detail.text, "html.parser")
        textarea = detail_soup.select_one("#id_html_source")
        name_input = detail_soup.select_one("#id_name")
        if textarea is None:
            raise RuntimeError(f"{base_url}{path}: HTML source textarea missing")
        template_id = int(path.rstrip("/").rsplit("/", 1)[1])
        name = name_input.get("value", "") if name_input else f"Template {template_id}"
        return {
            "source": f"{environment}:template:{template_id}",
            "name": name,
            "kind": "dashboard-template",
            "html": textarea.get_text(),
        }

    rows = []
    with ThreadPoolExecutor(max_workers=min(6, len(paths) or 1)) as pool:
        futures = {pool.submit(fetch, path): path for path in paths}
        for completed, future in enumerate(as_completed(futures), 1):
            rows.append(future.result())
            print(
                json.dumps(
                    {
                        "collected": environment,
                        "progress": completed,
                        "total": len(paths),
                    }
                ),
                flush=True,
            )
    return sorted(rows, key=lambda row: int(row["source"].rsplit(":", 1)[1]))


def corpus_row(source, name, kind, html):
    return {
        "source": source,
        "name": name,
        "kind": kind,
        "bytes": len(html.encode()),
        "sha256": hashlib.sha256(html.encode()).hexdigest(),
        "html": html,
    }


def collect(output):
    sys.path.insert(0, str(WORKSPACE))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cms_platform.settings")
    import django

    django.setup()
    from dashboard.views import STARTER_TEMPLATE_HTML

    entries = []
    for path in sorted((WORKSPACE / "samples").glob("**/*.html")):
        entries.append(
            corpus_row(
                f"repo:{path.relative_to(WORKSPACE)}",
                path.name,
                "repo-sample",
                path.read_text(),
            )
        )
    entries.append(
        corpus_row(
            "repo:dashboard.views.STARTER_TEMPLATE_HTML",
            "Starter template",
            "repo-constant",
            STARTER_TEMPLATE_HTML,
        )
    )
    entries.append(
        corpus_row(
            "phase9:failed-production-smoke",
            "Failed production smoke sample",
            "diagnostic-sample",
            FAILED_SMOKE_HTML,
        )
    )

    staging_env = load_dokploy_env()
    staging_password = staging_env.get("DJANGO_SUPERUSER_PASSWORD") or ""
    if not staging_password:
        raise RuntimeError("Staging DJANGO_SUPERUSER_PASSWORD is not set")
    staging = login("https://staging.sites.katek.app", "admin", staging_password)
    for row in dashboard_templates(
        "https://staging.sites.katek.app", staging, "staging"
    ):
        entries.append(corpus_row(**row))
    staging_password = None

    prod_username, prod_password = production_credential()
    production = login("https://sites.katek.app", prod_username, prod_password)
    for row in dashboard_templates("https://sites.katek.app", production, "production"):
        entries.append(corpus_row(**row))
    prod_password = None

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "entries": entries,
        "summary": {
            "entries": len(entries),
            "unique_html": len({row["sha256"] for row in entries}),
            "by_kind": {
                kind: sum(row["kind"] == kind for row in entries)
                for kind in sorted({row["kind"] for row in entries})
            },
        },
    }
    json_write(output, payload)
    safe_summary = {
        "output": str(output),
        **payload["summary"],
        "sources": [
            {k: row[k] for k in ("source", "name", "kind", "bytes", "sha256")}
            for row in entries
        ],
    }
    print(json.dumps(safe_summary, indent=2))


def refresh_local(corpus_path, output):
    """Refresh repository-owned rows while preserving the remote corpus."""
    sys.path.insert(0, str(WORKSPACE))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cms_platform.settings")
    import django

    django.setup()
    from dashboard.views import STARTER_TEMPLATE_HTML

    payload = json.loads(corpus_path.read_text())
    replacements = {
        "repo:dashboard.views.STARTER_TEMPLATE_HTML": corpus_row(
            "repo:dashboard.views.STARTER_TEMPLATE_HTML",
            "Starter template",
            "repo-constant",
            STARTER_TEMPLATE_HTML,
        ),
        "phase9:failed-production-smoke": corpus_row(
            "phase9:failed-production-smoke",
            "Failed production smoke sample",
            "diagnostic-sample",
            FAILED_SMOKE_HTML,
        ),
    }
    for path in sorted((WORKSPACE / "samples").glob("**/*.html")):
        source = f"repo:{path.relative_to(WORKSPACE)}"
        replacements[source] = corpus_row(
            source,
            path.name,
            "repo-sample",
            path.read_text(),
        )

    refreshed = [
        replacements.get(entry["source"], entry) for entry in payload["entries"]
    ]
    payload["entries"] = refreshed
    json_write(output, payload)
    print(
        json.dumps(
            {
                "output": str(output),
                "entries": len(refreshed),
                "local_rows_refreshed": sum(
                    entry["source"] in replacements for entry in refreshed
                ),
            }
        )
    )


def setup_tree(tree):
    sys.path.insert(0, str(tree.resolve()))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cms_platform.settings")
    import django

    django.setup()
    from django.test import override_settings
    from core.parser import build_schema
    from core.services import annotator

    return override_settings, build_schema, annotator


def strip_annotations(html):
    return ANNOTATION_ATTR_RE.sub("", html)


def schema_metrics(build_schema, html):
    schema = build_schema(html)
    sections = schema.get("sections", [])
    fields = [field for section in sections for field in section.get("fields", [])]
    nonbrand_sections = [section for section in sections if section.get("id") != "brand"]
    nonbrand_fields = [
        field for section in nonbrand_sections for field in section.get("fields", [])
    ]
    return {
        "sections": len(sections),
        "fields": len(fields),
        "images": sum(field.get("type") == "image" for field in fields),
        "field_ids": [field.get("id") for field in fields],
        "nonbrand_sections": len(nonbrand_sections),
        "nonbrand_fields": len(nonbrand_fields),
        "nonbrand_images": sum(
            field.get("type") == "image" for field in nonbrand_fields
        ),
    }


def structural_metrics(annotator, raw_html):
    slimmed, _ = annotator._strip_blocks(raw_html)
    slimmed, _ = annotator._strip_data_uris(slimmed)
    soup = BeautifulSoup(slimmed, "html.parser")
    refs = {}
    for idx, tag in enumerate(soup.find_all(True)):
        tag["data-cms-ref"] = str(idx)
        refs[str(idx)] = tag
    root = annotator._find_split_root(soup)
    blocks = [c for c in root.children if getattr(c, "name", None)]
    return {
        "elements": len(refs),
        "semantic_sections": len(soup.find_all(["section", "article"])),
        "split_root": getattr(root, "name", None),
        "split_blocks": len(blocks),
        "split_block_tags": [block.name for block in blocks],
        "single_root_chain": len(blocks) == 1,
    }


def run_one(entry, api_key, model, effort, override_settings, build_schema, annotator):
    raw_html = strip_annotations(entry["html"])
    ground_truth = schema_metrics(build_schema, entry["html"])
    trace = {
        "applied": 0,
        "promoted": 0,
        "salvaged": 0,
        "reconciled": 0,
        "dropped": 0,
        "text_backfilled": 0,
        "image_backfilled": 0,
        "chunk_results": [],
    }
    original_chunks = annotator._annotate_chunks_parallel
    original_apply = annotator._apply_annotations
    original_recover = getattr(annotator, "_recover_grouped_orphan_fields", None)
    original_reconcile = getattr(annotator, "_reconcile_annotated_fields", None)
    original_text = annotator._backfill_missed_text_fields
    original_image = getattr(annotator, "_backfill_missed_image_fields", None)

    def capture_chunks(*args, **kwargs):
        result = original_chunks(*args, **kwargs)
        trace["chunk_results"] = result
        return result

    def capture_apply(*args, **kwargs):
        trace["applied"] = original_apply(*args, **kwargs)
        return trace["applied"]

    def capture_reconcile(*args, **kwargs):
        result = original_reconcile(*args, **kwargs)
        trace["reconciled"], trace["dropped"] = result
        return result

    def capture_recover(*args, **kwargs):
        result = original_recover(*args, **kwargs)
        trace["promoted"], trace["salvaged"] = result
        return result

    def capture_text(*args, **kwargs):
        trace["text_backfilled"] = original_text(*args, **kwargs)
        return trace["text_backfilled"]

    def capture_image(*args, **kwargs):
        trace["image_backfilled"] = original_image(*args, **kwargs)
        return trace["image_backfilled"]

    started = time.perf_counter()
    result_html = ""
    error = None
    with override_settings(
        OPENAI_API_KEY=api_key,
        OPENAI_ANNOTATE_MODEL=model,
        OPENAI_ANNOTATE_REASONING_EFFORT=effort,
    ):
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(annotator, "_annotate_chunks_parallel", side_effect=capture_chunks)
            )
            stack.enter_context(
                patch.object(annotator, "_apply_annotations", side_effect=capture_apply)
            )
            if original_recover:
                stack.enter_context(
                    patch.object(
                        annotator,
                        "_recover_grouped_orphan_fields",
                        side_effect=capture_recover,
                    )
                )
            stack.enter_context(
                patch.object(annotator, "_backfill_missed_text_fields", side_effect=capture_text)
            )
            if original_reconcile:
                stack.enter_context(
                    patch.object(
                        annotator,
                        "_reconcile_annotated_fields",
                        side_effect=capture_reconcile,
                    )
                )
            if original_image:
                stack.enter_context(
                    patch.object(
                        annotator,
                        "_backfill_missed_image_fields",
                        side_effect=capture_image,
                    )
                )
            try:
                if hasattr(annotator, "annotate_html_result"):
                    annotation = annotator.annotate_html_result(raw_html)
                    result_html = annotation.html
                else:
                    result_html = annotator.annotate_html(raw_html)
            except Exception as exc:  # exact application error is report data
                error = str(exc)
    latency = time.perf_counter() - started

    usage = {key: 0 for key in (
        "prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens"
    )}
    if hasattr(annotator, "_merge_chunk_usage"):
        usage.update(annotator._merge_chunk_usage(trace["chunk_results"]))
    price = PRICES.get(model, {"input": 0, "output": 0})
    cost = (
        usage["prompt_tokens"] * price["input"]
        + usage["completion_tokens"] * price["output"]
    ) / 1_000_000
    produced = schema_metrics(build_schema, result_html) if result_html else {
        "sections": 0,
        "fields": 0,
        "images": 0,
        "field_ids": [],
        "nonbrand_sections": 0,
        "nonbrand_fields": 0,
        "nonbrand_images": 0,
    }
    marker_count = (
        len(BeautifulSoup(result_html, "lxml").find_all(attrs={"data-edit": True}))
        if result_html else 0
    )
    model_data = (
        annotator._merge_chunk_results(trace["chunk_results"])
        if trace["chunk_results"] else {"sections": [], "fields": []}
    )
    model_data.pop("_usage", None)
    return {
        "source": entry["source"],
        "name": entry["name"],
        "kind": entry["kind"],
        "bytes": entry["bytes"],
        "raw_bytes": len(raw_html.encode()),
        "ground_truth": ground_truth,
        "structure": structural_metrics(annotator, raw_html),
        "outcome": "error" if error else "ok",
        "error": error,
        "produced": produced,
        "promoted": trace["promoted"],
        "salvaged": trace["salvaged"],
        "reconciled": trace["reconciled"],
        "dropped": trace["dropped"],
        "backfilled": trace["text_backfilled"] + trace["image_backfilled"],
        "text_backfilled": trace["text_backfilled"],
        "image_backfilled": trace["image_backfilled"],
        "fields_applied": trace["applied"],
        "marker_count": marker_count,
        "marker_schema_parity": marker_count == produced["nonbrand_fields"],
        **usage,
        "cost_usd": round(cost, 8),
        "latency_seconds": round(latency, 3),
        "model_data": model_data,
        "chunk_results": trace["chunk_results"],
    }


def run_corpus(args):
    override_settings, build_schema, annotator = setup_tree(args.tree)
    corpus = json.loads(args.corpus.read_text())
    entries = corpus["entries"]
    if args.failures_from:
        prior = json.loads(args.failures_from.read_text())
        failures = {row["source"] for row in prior["rows"] if row["outcome"] == "error"}
        entries = [entry for entry in entries if entry["source"] in failures]
    if args.sources:
        wanted = set(args.sources)
        entries = [entry for entry in entries if entry["source"] in wanted]
    if args.shard_count > 1:
        entries = [
            entry
            for index, entry in enumerate(entries)
            if index % args.shard_count == args.shard_index
        ]
    env = load_dokploy_env()
    api_key = env.get("OPENAI_API_KEY") or ""
    if not api_key:
        raise RuntimeError("Staging OPENAI_API_KEY is not set")

    rows = []
    for index, entry in enumerate(entries, 1):
        print(
            json.dumps({"progress": index, "total": len(entries), "source": entry["source"]}),
            flush=True,
        )
        rows.append(
            run_one(
                entry,
                api_key,
                args.model,
                args.effort,
                override_settings,
                build_schema,
                annotator,
            )
        )
    api_key = None
    payload = {
        "model": args.model,
        "reasoning_effort": args.effort if args.model == "gpt-5.6-luna" else None,
        "tree": str(args.tree),
        "rows": rows,
        "summary": {
            "total": len(rows),
            "ok": sum(row["outcome"] == "ok" for row in rows),
            "errors": sum(row["outcome"] == "error" for row in rows),
            "cost_usd": round(sum(row["cost_usd"] for row in rows), 8),
            "tokens": sum(row["total_tokens"] for row in rows),
            "latency_seconds": round(sum(row["latency_seconds"] for row in rows), 3),
        },
    }
    json_write(args.output, payload)
    print(json.dumps({"complete": payload["summary"], "output": str(args.output)}))


def replay(args):
    override_settings, build_schema, annotator = setup_tree(args.tree)
    corpus = {row["source"]: row for row in json.loads(args.corpus.read_text())["entries"]}
    current = json.loads(args.results.read_text())
    rows = []
    selected = [row for row in current["rows"] if row["outcome"] == "error"]
    if args.all_rows:
        selected = current["rows"]
    elif args.sources:
        selected = [row for row in current["rows"] if row["source"] in set(args.sources)]
    for failed in selected:
        entry = corpus[failed["source"]]
        raw_html = strip_annotations(entry["html"])
        chunk_results = failed["chunk_results"]
        with override_settings(OPENAI_API_KEY="replay-only"):
            with patch.object(annotator, "_make_openai_client", return_value=object()), patch.object(
                annotator,
                "_annotate_chunks_parallel",
                return_value=chunk_results,
            ):
                started = time.perf_counter()
                try:
                    output = annotator.annotate_html(raw_html)
                    outcome = "ok"
                    error = None
                except Exception as exc:
                    output = ""
                    outcome = "error"
                    error = str(exc)
        metrics = schema_metrics(build_schema, output) if output else {
            "sections": 0,
            "fields": 0,
            "images": 0,
            "field_ids": [],
            "nonbrand_sections": 0,
            "nonbrand_fields": 0,
            "nonbrand_images": 0,
        }
        rows.append(
            {
                "source": failed["source"],
                "outcome": outcome,
                "error": error,
                "produced": metrics,
                "latency_seconds": round(time.perf_counter() - started, 3),
                "method": "exact current model JSON replayed through selected tree",
            }
        )
    payload = {
        "tree": str(args.tree),
        "rows": rows,
        "summary": {
            "total": len(rows),
            "ok": sum(row["outcome"] == "ok" for row in rows),
            "errors": sum(row["outcome"] == "error" for row in rows),
        },
    }
    json_write(args.output, payload)
    print(json.dumps({"complete": payload["summary"], "output": str(args.output)}))


def merge_results(args):
    payloads = [json.loads(path.read_text()) for path in args.inputs]
    rows = [row for payload in payloads for row in payload["rows"]]
    rows.sort(key=lambda row: row["source"])
    first = payloads[0]
    payload = {
        "model": first.get("model"),
        "reasoning_effort": first.get("reasoning_effort"),
        "tree": first.get("tree"),
        "rows": rows,
        "summary": {
            "total": len(rows),
            "ok": sum(row["outcome"] == "ok" for row in rows),
            "errors": sum(row["outcome"] == "error" for row in rows),
            "cost_usd": round(sum(row.get("cost_usd", 0) for row in rows), 8),
            "tokens": sum(row.get("total_tokens", 0) for row in rows),
            "latency_seconds": round(
                sum(row.get("latency_seconds", 0) for row in rows), 3
            ),
        },
    }
    json_write(args.output, payload)
    print(json.dumps({"complete": payload["summary"], "output": str(args.output)}))


def markdown_report(args):
    payload = json.loads(args.results.read_text())
    lines = [
        "# Annotation corpus results",
        "",
        f"Model: `{payload.get('model')}`; reasoning effort: "
        f"`{payload.get('reasoning_effort') or 'n/a'}`.",
        "",
        "| Source | Name | Bytes | GT S/F/I | Outcome | Produced S/F/I | "
        "Promoted | Salvaged | Reconciled | Dropped | Backfilled | Parity | "
        "Tokens | Cost USD | Latency s |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|:---:|---:|---:|---:|",
    ]
    failures = []
    for row in payload["rows"]:
        gt = row["ground_truth"]
        produced = row["produced"]
        source = row["source"].replace("|", "\\|")
        name = row["name"].replace("|", "\\|")
        outcome = row["outcome"]
        if outcome == "error":
            failures.append((source, row["error"]))
            outcome = f"error [{len(failures)}]"
        lines.append(
            "| {source} | {name} | {bytes} | {gts}/{gtf}/{gti} | {outcome} | "
            "{ps}/{pf}/{pi} | {promoted} | {salvaged} | {reconciled} | "
            "{dropped} | {backfilled} | {parity} | {tokens} | {cost:.8f} | "
            "{latency:.3f} |".format(
                source=source,
                name=name,
                bytes=row["bytes"],
                gts=gt["nonbrand_sections"],
                gtf=gt["nonbrand_fields"],
                gti=gt["nonbrand_images"],
                outcome=outcome,
                ps=produced["nonbrand_sections"],
                pf=produced["nonbrand_fields"],
                pi=produced["nonbrand_images"],
                promoted=row.get("promoted", 0),
                salvaged=row.get("salvaged", 0),
                reconciled=row["reconciled"],
                dropped=row["dropped"],
                backfilled=row["backfilled"],
                parity="yes" if row["marker_schema_parity"] else "no",
                tokens=row["total_tokens"],
                cost=row["cost_usd"],
                latency=row["latency_seconds"],
            )
        )
    if failures:
        lines.extend(["", "## Errors", ""])
        for index, (source, error) in enumerate(failures, 1):
            lines.append(f"{index}. `{source}`: {error}")
    lines.extend(
        [
            "",
            "## Aggregate",
            "",
            "```json",
            json.dumps(payload["summary"], indent=2),
            "```",
            "",
            "The result JSON retains each ground-truth and produced field-id list.",
        ]
    )
    args.output.write_text("\n".join(lines) + "\n")
    print(json.dumps({"output": str(args.output), "rows": len(payload["rows"])}))


def parse_args():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--output", type=Path, required=True)

    refresh_parser = subparsers.add_parser("refresh-local")
    refresh_parser.add_argument("--corpus", type=Path, required=True)
    refresh_parser.add_argument("--output", type=Path, required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--corpus", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--tree", type=Path, default=WORKSPACE)
    run_parser.add_argument("--model", required=True)
    run_parser.add_argument("--effort", default="medium")
    run_parser.add_argument("--failures-from", type=Path)
    run_parser.add_argument("--source", dest="sources", action="append")
    run_parser.add_argument("--shard-count", type=int, default=1)
    run_parser.add_argument("--shard-index", type=int, default=0)

    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("--corpus", type=Path, required=True)
    replay_parser.add_argument("--results", type=Path, required=True)
    replay_parser.add_argument("--tree", type=Path, required=True)
    replay_parser.add_argument("--output", type=Path, required=True)
    replay_parser.add_argument("--source", dest="sources", action="append")
    replay_parser.add_argument("--all", dest="all_rows", action="store_true")

    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--inputs", type=Path, nargs="+", required=True)
    merge_parser.add_argument("--output", type=Path, required=True)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--results", type=Path, required=True)
    report_parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.command == "collect":
        collect(args.output)
    elif args.command == "refresh-local":
        refresh_local(args.corpus, args.output)
    elif args.command == "run":
        run_corpus(args)
    elif args.command == "replay":
        replay(args)
    elif args.command == "merge":
        merge_results(args)
    else:
        markdown_report(args)


if __name__ == "__main__":
    main()
