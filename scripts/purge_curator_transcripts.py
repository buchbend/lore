#!/usr/bin/env python3
"""Purge curator subprocess transcripts from the ledger and clean up polluted notes.

Curator A's `claude -p` subprocess transcripts were stored under
~/.claude/projects/ and re-ingested as user sessions. This script:

1. Identifies curator transcripts (first line is a queue-operation)
2. Removes them from the transcript ledger
3. Resets genuine transcripts' session_note/curator_a_run so they can be re-filed
4. Removes polluted session notes that contain curator content
5. Deletes the curator transcript files from ~/.claude/projects/

Run with --dry-run first to see what would change.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def is_curator_transcript(path: Path) -> bool:
    """Check if a transcript file is a curator subprocess transcript."""
    if not path.exists():
        return False
    try:
        with open(path) as f:
            first = f.readline()
        return "queue-operation" in first
    except OSError:
        return False


def main(lore_root: str, *, dry_run: bool = True, target_dir: str = "system-integration") -> None:
    root = Path(lore_root)
    ledger_path = root / ".lore" / "transcript-ledger.json"
    ledger = json.loads(ledger_path.read_text())

    curator_keys = []
    genuine_keys_to_reset = []
    polluted_notes: set[str] = set()

    for key, entry in ledger.items():
        directory = entry.get("directory", "")
        if target_dir not in directory:
            continue

        tid = entry.get("transcript_id", "")
        path = Path(entry.get("path", ""))

        if is_curator_transcript(path):
            curator_keys.append(key)
            sn = entry.get("session_note", "")
            if sn:
                polluted_notes.add(sn)
        else:
            # Genuine transcript — if it was filed into a polluted note,
            # reset it so curator A can re-file it cleanly.
            sn = entry.get("session_note", "")
            if sn in polluted_notes or not sn:
                genuine_keys_to_reset.append(key)

    # Second pass: check if genuine transcripts point to polluted notes
    for key in list(genuine_keys_to_reset):
        sn = ledger[key].get("session_note", "")
        if sn and sn not in polluted_notes:
            genuine_keys_to_reset.remove(key)

    # Also pick up genuine transcripts pointing to polluted notes found above
    for key, entry in ledger.items():
        directory = entry.get("directory", "")
        if target_dir not in directory:
            continue
        if key in curator_keys:
            continue
        sn = entry.get("session_note", "")
        if sn in polluted_notes and key not in genuine_keys_to_reset:
            genuine_keys_to_reset.append(key)

    print(f"Curator transcripts to remove from ledger: {len(curator_keys)}")
    print(f"Genuine transcripts to reset for re-filing: {len(genuine_keys_to_reset)}")
    print(f"Polluted notes to remove: {len(polluted_notes)}")
    for n in sorted(polluted_notes):
        print(f"  {n}")

    if dry_run:
        print("\n--dry-run: no changes made. Run with --apply to execute.")
        return

    # 1. Remove curator entries from ledger
    for key in curator_keys:
        del ledger[key]
    print(f"Removed {len(curator_keys)} curator entries from ledger")

    # 2. Reset genuine entries for re-filing
    for key in genuine_keys_to_reset:
        ledger[key]["session_note"] = None
        ledger[key]["curator_a_run"] = None
        ledger[key]["noteworthy"] = None
        ledger[key]["digested_hash"] = None
        ledger[key]["digested_index_hint"] = None
    print(f"Reset {len(genuine_keys_to_reset)} genuine entries for re-filing")

    # 3. Write updated ledger
    tmp = ledger_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=2))
    tmp.replace(ledger_path)
    print("Ledger updated")

    # 4. Remove polluted note files
    wiki_root = root / "wiki"
    for note_wikilink in polluted_notes:
        slug = note_wikilink.strip("[]")
        for md in wiki_root.rglob(f"{slug}.md"):
            print(f"Removing: {md}")
            md.unlink()

    # 5. Delete curator transcript files
    deleted = 0
    for key in curator_keys:
        # Reconstruct from the entry we already removed
        pass  # entries already deleted from dict

    # Re-read original to get paths
    original = json.loads(ledger_path.read_text())
    # We already wrote the pruned version, so read from the curator_keys we saved
    # Actually, let's just scan the project dir for queue-operation files
    projects_dir = Path.home() / ".claude" / "projects"
    for d in projects_dir.iterdir():
        if target_dir not in d.name:
            continue
        for f in d.glob("*.jsonl"):
            if is_curator_transcript(f):
                print(f"Deleting: {f}")
                f.unlink()
                deleted += 1
    print(f"Deleted {deleted} curator transcript files")

    # 6. Clean synced copies
    for wiki_dir in wiki_root.iterdir():
        transcripts_dir = wiki_dir / ".transcripts"
        if not transcripts_dir.exists():
            continue
        cleaned = 0
        for f in transcripts_dir.glob("*.jsonl"):
            if is_curator_transcript(f):
                f.unlink()
                cleaned += 1
        if cleaned:
            print(f"Cleaned {cleaned} synced curator transcripts from {wiki_dir.name}/.transcripts/")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lore_root", help="Path to LORE_ROOT (vault)")
    parser.add_argument("--apply", action="store_true", help="Actually make changes (default: dry-run)")
    parser.add_argument("--target-dir", default="system-integration", help="Directory name to filter by")
    args = parser.parse_args()
    main(args.lore_root, dry_run=not args.apply, target_dir=args.target_dir)
