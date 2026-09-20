#!/usr/bin/env python3
"""Analyse a Telegram Desktop JSON export without loading/truncating it in chat.

Usage:
    python scripts/analyze_telegram_export.py result(2)(1).json
    python scripts/analyze_telegram_export.py result(2)(1).json --output telegram_analysis.json

The script never invents product fields. A "potential product publication" is only a
message entry (not a service event) that has a photo, media/file, or non-empty text,
and is not in a clearly non-product topic such as reviews or ordering instructions.
The report preserves the original IDs and topic names for manual validation.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

NON_PRODUCT_TOPIC_TERMS = (
    "avis", "commande", "comment passer", "comment commander", "contact",
    "paiement", "règlement", "reglement", "information", "info", "groupe",
)


def as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(as_text(x.get("text", "") if isinstance(x, dict) else x) for x in value)
    return ""


def is_media_message(message: dict[str, Any]) -> bool:
    """Count explicit exported media references, including Telegram video thumbnails."""
    media_keys = (
        "photo", "file", "thumbnail", "video", "animation", "audio", "voice",
        "document", "media_type", "mime_type",
    )
    return any(message.get(key) not in (None, "", [], {}) for key in media_keys)


def topic_name_is_non_product(name: str) -> bool:
    normalized = name.casefold()
    return any(term in normalized for term in NON_PRODUCT_TOPIC_TERMS)


def load_export(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        raise ValueError("Le fichier doit contenir un objet JSON avec un tableau messages.")
    return data


def analyse(data: dict[str, Any], source: Path) -> dict[str, Any]:
    messages = data["messages"]
    topic_names: dict[int, str] = {}
    for message in messages:
        if message.get("type") == "service" and message.get("action") in {
            "topic_created", "topic_edited",
        }:
            title = str(message.get("title") or message.get("name") or "").strip()
            if title:
                topic_names[int(message["id"])] = title

    # Telegram exports represent topic membership for ordinary messages through
    # reply_to_message_id, which points to the topic creation/service message.
    topic_messages: dict[str, list[int]] = defaultdict(list)
    unassigned: list[int] = []
    message_records: list[dict[str, Any]] = []
    media_count = 0
    product_candidates: list[int] = []

    for message in messages:
        message_id = message.get("id")
        if not isinstance(message_id, int):
            continue
        is_service = message.get("type") == "service"
        reply_id = message.get("reply_to_message_id")
        topic_id = reply_id if isinstance(reply_id, int) and reply_id in topic_names else None
        topic_name = topic_names.get(topic_id, "Sans topic") if topic_id is not None else "Sans topic"
        media = is_media_message(message)
        text = as_text(message.get("text")).strip()
        if media:
            media_count += 1
        if topic_id is None:
            unassigned.append(message_id)
        else:
            topic_messages[str(topic_id)].append(message_id)

        # Candidate means potentially importable, not automatically sellable.
        # Service records and clearly administrative/non-product topics are excluded.
        candidate = (
            not is_service
            and (media or bool(text))
            and not topic_name_is_non_product(topic_name)
        )
        if candidate:
            product_candidates.append(message_id)
        message_records.append({
            "id": message_id,
            "type": message.get("type"),
            "topic_id": topic_id,
            "topic_name": topic_name,
            "has_media_reference": media,
            "has_text": bool(text),
            "potential_product": candidate,
        })

    # Include every detected topic, including empty topics.
    topics = []
    for topic_id, name in sorted(topic_names.items()):
        ids = topic_messages.get(str(topic_id), [])
        topics.append({
            "id": topic_id,
            "name": name,
            "message_count": len(ids),
            "message_ids": ids,
            "media_message_count": sum(
                1 for record in message_records
                if record["topic_id"] == topic_id and record["has_media_reference"]
            ),
            "potential_product_count": sum(
                1 for record in message_records
                if record["topic_id"] == topic_id and record["potential_product"]
            ),
        })

    type_counts = Counter(str(message.get("type", "")) for message in messages)
    return {
        "source_file": str(source),
        "export_name": data.get("name"),
        "export_type": data.get("type"),
        "export_chat_id": data.get("id"),
        "total_messages": len(messages),
        "service_message_count": sum(1 for message in messages if message.get("type") == "service"),
        "ordinary_publication_count": sum(1 for message in messages if message.get("type") != "service"),
        "media_reference_message_count": media_count,
        "potential_product_publication_count": len(product_candidates),
        "unassigned_message_count": len(unassigned),
        "unassigned_message_ids": unassigned,
        "message_type_counts": dict(sorted(type_counts.items())),
        "topics": topics,
        "potential_product_message_ids": product_candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, help="Write the complete report as JSON.")
    args = parser.parse_args()
    try:
        data = load_export(args.input)
        report = analyse(data, args.input)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
