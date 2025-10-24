#!/usr/bin/env python3
"""
Reconciliation script to compare inputfile.txt and finished.dat
Shows what's been processed, what's pending, and identifies discrepancies.
"""

import sys
from pathlib import Path
from typing import Set, List
from urllib.parse import urlparse, parse_qs


def extract_video_id(url: str) -> str:
    """
    Extract YouTube video ID from various URL formats.
    Handles watch?v=, shorts/, live/, etc.
    """
    url = url.strip()
    
    # Handle various YouTube URL formats
    if "youtube.com/watch" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        return qs.get('v', [''])[0]
    elif "youtube.com/shorts/" in url:
        return url.split("/shorts/")[-1].split("?")[0]
    elif "youtube.com/live/" in url:
        return url.split("/live/")[-1].split("?")[0]
    elif "youtu.be/" in url:
        return url.split("youtu.be/")[-1].split("?")[0]
    
    # Return the URL as-is if we can't extract an ID (for invalid URLs)
    return url


def load_urls(filepath: Path) -> tuple[List[str], List[str], Set[str]]:
    """
    Load URLs from file.
    Returns: (raw_urls, video_ids, unique_video_ids)
    """
    if not filepath.exists():
        print(f"[error] File not found: {filepath}", file=sys.stderr)
        sys.exit(1)
    
    with open(filepath, 'r', encoding='utf-8') as f:
        raw_urls = [line.strip() for line in f if line.strip()]
    
    video_ids = [extract_video_id(url) for url in raw_urls]
    unique_ids = set(video_ids)
    
    return raw_urls, video_ids, unique_ids


def find_duplicates(urls: List[str], video_ids: List[str]) -> dict:
    """Find duplicate video IDs in a list."""
    seen = {}
    duplicates = {}
    
    for idx, (url, vid_id) in enumerate(zip(urls, video_ids), 1):
        if vid_id in seen:
            if vid_id not in duplicates:
                duplicates[vid_id] = [seen[vid_id]]
            duplicates[vid_id].append((idx, url))
        else:
            seen[vid_id] = (idx, url)
    
    return duplicates


def find_invalid_urls(urls: List[str]) -> List[tuple]:
    """Find URLs that don't look like valid YouTube URLs."""
    invalid = []
    for idx, url in enumerate(urls, 1):
        if not url.startswith("https://"):
            invalid.append((idx, url, "Not HTTPS"))
        elif "youtube.com" not in url and "youtu.be" not in url:
            invalid.append((idx, url, "Not a YouTube URL"))
        elif "results?search" in url:
            invalid.append((idx, url, "Search results page, not a video"))
    
    return invalid


def scan_output_directory(output_dir: Path) -> tuple[Set[str], List[Path]]:
    """
    Scan the output directory for existing JSON transcript files.
    Returns: (set of video IDs with transcripts, list of JSON file paths)
    """
    if not output_dir.exists():
        return set(), []
    
    json_files = list(output_dir.glob("*.json"))
    video_ids_with_transcripts = set()
    
    for json_file in json_files:
        # The filename should be {video_id}.json
        video_id = json_file.stem
        video_ids_with_transcripts.add(video_id)
    
    return video_ids_with_transcripts, json_files


def main():
    input_file = Path("inputfile.txt")
    finished_file = Path("finished.dat")
    output_dir = Path("out")
    
    print("=" * 80)
    print("YOUTUBE TRANSCRIPTION RECONCILIATION REPORT")
    print("=" * 80)
    print()
    
    # Load input file
    print(f"📄 Loading {input_file}...")
    input_urls, input_ids, input_unique = load_urls(input_file)
    print(f"   Total lines: {len(input_urls)}")
    print(f"   Unique videos: {len(input_unique)}")
    print(f"   Duplicates: {len(input_urls) - len(input_unique)}")
    print()
    
    # Scan output directory
    print(f"📁 Scanning {output_dir}/ for transcript files...")
    transcripts_ids, transcript_files = scan_output_directory(output_dir)
    print(f"   Found {len(transcript_files)} JSON transcript files")
    print(f"   Unique video IDs: {len(transcripts_ids)}")
    print()
    
    # Load finished file
    print(f"✅ Loading {finished_file}...")
    finished_urls, finished_ids, finished_unique = load_urls(finished_file)
    print(f"   Total lines: {len(finished_urls)}")
    print(f"   Unique videos: {len(finished_unique)}")
    print(f"   Duplicates: {len(finished_urls) - len(finished_unique)}")
    print()
    
    # === DISCREPANCY ANALYSIS ===
    print("=" * 80)
    print("🔍 DISCREPANCY ANALYSIS")
    print("=" * 80)
    print()
    
    # Videos in finished.dat but NOT in inputfile.txt
    extra_in_finished = finished_unique - input_unique
    if extra_in_finished:
        print(f"⚠️  WARNING: {len(extra_in_finished)} videos in finished.dat are NOT in inputfile.txt!")
        print("   This suggests finished.dat contains entries from a previous run or different input.")
        print()
        print("   Videos in finished.dat but not in inputfile.txt:")
        for vid_id in sorted(extra_in_finished):
            # Find the URL in finished file
            for url, fid in zip(finished_urls, finished_ids):
                if fid == vid_id:
                    print(f"      - {url}")
                    break
        print()
    
    # Videos in inputfile.txt but NOT in finished.dat
    pending = input_unique - finished_unique
    if pending:
        print(f"📋 PENDING: {len(pending)} videos in inputfile.txt not yet in finished.dat")
        print("   (These still need to be processed)")
        print()
    
    # Completed videos
    completed = input_unique & finished_unique
    print(f"✅ COMPLETED: {len(completed)} videos from inputfile.txt are in finished.dat")
    print()
    
    # === OUTPUT DIRECTORY ANALYSIS ===
    print("=" * 80)
    print("💾 OUTPUT DIRECTORY ANALYSIS")
    print("=" * 80)
    print()
    
    # Videos marked finished but no transcript file exists
    finished_but_no_file = finished_unique - transcripts_ids
    if finished_but_no_file:
        print(f"⚠️  WARNING: {len(finished_but_no_file)} videos in finished.dat have NO transcript file!")
        print("   These are marked as done but the JSON files are missing.")
        print()
        # Show a few examples
        examples = sorted(finished_but_no_file)[:10]
        for vid_id in examples:
            print(f"      - Video ID: {vid_id} (expected: out/{vid_id}.json)")
        if len(finished_but_no_file) > 10:
            print(f"      ... and {len(finished_but_no_file) - 10} more")
        print()
    else:
        print("✅ All videos in finished.dat have corresponding transcript files")
        print()
    
    # Transcript files that aren't in finished.dat
    orphaned_transcripts = transcripts_ids - finished_unique
    if orphaned_transcripts:
        print(f"⚠️  WARNING: {len(orphaned_transcripts)} transcript files exist but are NOT in finished.dat!")
        print("   These files were transcribed but not tracked.")
        print()
        for vid_id in sorted(orphaned_transcripts):
            print(f"      - out/{vid_id}.json")
        print()
    else:
        print("✅ All transcript files are properly tracked in finished.dat")
        print()
    
    # ACTUAL completion status (based on files that exist)
    actually_completed = input_unique & transcripts_ids
    actually_pending = input_unique - transcripts_ids
    
    print(f"📊 ACTUAL STATUS (based on transcript files):")
    print(f"   Videos from input WITH transcripts: {len(actually_completed)} ({len(actually_completed)/len(input_unique)*100:.1f}%)")
    print(f"   Videos from input WITHOUT transcripts: {len(actually_pending)} ({len(actually_pending)/len(input_unique)*100:.1f}%)")
    print()
    
    # Discrepancies between finished.dat and actual files
    if finished_but_no_file or orphaned_transcripts:
        print(f"⚠️  LEDGER MISMATCH: finished.dat is out of sync with actual transcript files!")
        if finished_but_no_file:
            print(f"   - {len(finished_but_no_file)} entries in finished.dat without files (possibly failed or deleted)")
        if orphaned_transcripts:
            print(f"   - {len(orphaned_transcripts)} transcript files not recorded in finished.dat")
        print()
    
    # === DUPLICATE ANALYSIS ===
    print("=" * 80)
    print("🔄 DUPLICATE ANALYSIS")
    print("=" * 80)
    print()
    
    input_dups = find_duplicates(input_urls, input_ids)
    if input_dups:
        print(f"⚠️  {len(input_dups)} duplicate video(s) found in inputfile.txt:")
        for vid_id, occurrences in sorted(input_dups.items()):
            print(f"   Video ID: {vid_id}")
            for line_no, url in occurrences:
                print(f"      Line {line_no}: {url}")
        print()
    else:
        print("✅ No duplicates found in inputfile.txt")
        print()
    
    finished_dups = find_duplicates(finished_urls, finished_ids)
    if finished_dups:
        print(f"⚠️  {len(finished_dups)} duplicate video(s) found in finished.dat:")
        for vid_id, occurrences in sorted(finished_dups.items()):
            print(f"   Video ID: {vid_id} - appears {len(occurrences)} times")
        print()
    else:
        print("✅ No duplicates found in finished.dat")
        print()
    
    # === INVALID URL ANALYSIS ===
    print("=" * 80)
    print("🚫 INVALID URL ANALYSIS")
    print("=" * 80)
    print()
    
    input_invalid = find_invalid_urls(input_urls)
    if input_invalid:
        print(f"⚠️  {len(input_invalid)} invalid URL(s) found in inputfile.txt:")
        for line_no, url, reason in input_invalid:
            print(f"   Line {line_no}: {url}")
            print(f"      Reason: {reason}")
        print()
    else:
        print("✅ All URLs in inputfile.txt appear valid")
        print()
    
    # === SUMMARY ===
    print("=" * 80)
    print("📊 SUMMARY")
    print("=" * 80)
    print()
    print(f"Input file unique videos:       {len(input_unique)}")
    print(f"Finished file unique videos:    {len(finished_unique)}")
    print(f"Transcript files found:         {len(transcripts_ids)}")
    print()
    print(f"According to finished.dat:")
    print(f"  Completed from input:         {len(completed)} ({len(completed)/len(input_unique)*100:.1f}%)")
    print(f"  Pending from input:           {len(pending)} ({len(pending)/len(input_unique)*100:.1f}%)")
    print(f"  Extra (not in input):         {len(extra_in_finished)}")
    print()
    print(f"According to actual transcript files:")
    print(f"  Completed from input:         {len(actually_completed)} ({len(actually_completed)/len(input_unique)*100:.1f}%)")
    print(f"  Pending from input:           {len(actually_pending)} ({len(actually_pending)/len(input_unique)*100:.1f}%)")
    print()
    print(f"Ledger sync status:")
    print(f"  Finished but no file:         {len(finished_but_no_file)}")
    print(f"  File but not in finished:     {len(orphaned_transcripts)}")
    print()
    
    # === RECOMMENDATIONS ===
    print("=" * 80)
    print("💡 RECOMMENDATIONS")
    print("=" * 80)
    print()
    
    rec_num = 1
    
    if finished_but_no_file:
        print(f"{rec_num}. ⚠️  CRITICAL: {len(finished_but_no_file)} videos marked finished but files are MISSING!")
        print(f"   ACTION: These need to be re-transcribed or removed from finished.dat")
        print()
        rec_num += 1
    
    if orphaned_transcripts:
        print(f"{rec_num}. 📝 {len(orphaned_transcripts)} transcript files exist but aren't tracked")
        print(f"   ACTION: Add these video IDs to finished.dat to track them properly")
        print()
        rec_num += 1
    
    if extra_in_finished:
        print(f"{rec_num}. ⚠️  ISSUE: finished.dat has entries from previous runs")
        print("   CAUSE: Likely contains entries from previous runs with different input files.")
        print("   FIX: Use the generated finished_clean.dat to sync with inputfile.txt")
        print()
        rec_num += 1
    
    if actually_pending:
        print(f"{rec_num}. 📝 You have {len(actually_pending)} videos left to transcribe (based on actual files)")
        print("   ACTION: Use the generated pending.txt file to process remaining videos")
        print()
        rec_num += 1
    
    if input_dups:
        print(f"{rec_num}. 🔄 Consider removing {len(input_dups)} duplicate(s) from inputfile.txt")
        print("   ACTION: Use the generated inputfile_clean.txt")
        print()
        rec_num += 1
    
    if input_invalid:
        print(f"{rec_num}. 🚫 Fix or remove {len(input_invalid)} invalid URL(s) from inputfile.txt")
        print()
        rec_num += 1
    
    # === GENERATE CLEAN FILES ===
    print("=" * 80)
    print("🔧 GENERATING CLEAN FILES")
    print("=" * 80)
    print()
    
    # Generate pending.txt (based on ACTUAL missing transcripts)
    if actually_pending:
        pending_path = Path("pending.txt")
        with open(pending_path, 'w', encoding='utf-8') as f:
            for url, vid_id in zip(input_urls, input_ids):
                if vid_id in actually_pending:
                    f.write(url + '\n')
        print(f"✅ Created {pending_path} with {len(actually_pending)} videos without transcripts")
    
    # Generate cleaned inputfile (deduplicated, no invalid)
    cleaned_path = Path("inputfile_clean.txt")
    seen_ids = set()
    clean_count = 0
    with open(cleaned_path, 'w', encoding='utf-8') as f:
        for url, vid_id in zip(input_urls, input_ids):
            # Skip invalid URLs
            is_invalid = any(vid_id == extract_video_id(inv_url) for _, inv_url, _ in input_invalid)
            if not is_invalid and vid_id not in seen_ids and vid_id:
                f.write(url + '\n')
                seen_ids.add(vid_id)
                clean_count += 1
    print(f"✅ Created {cleaned_path} with {clean_count} unique, valid videos")
    
    # Generate corrected finished.dat (based on actual transcript files)
    finished_corrected_path = Path("finished_corrected.dat")
    with open(finished_corrected_path, 'w', encoding='utf-8') as f:
        written = set()
        # First, add all entries from input that have transcript files
        for url, vid_id in zip(input_urls, input_ids):
            if vid_id in transcripts_ids and vid_id not in written:
                f.write(url + '\n')
                written.add(vid_id)
    print(f"✅ Created {finished_corrected_path} with {len(written)} videos (synced with actual transcript files)")
    
    # Generate a report of missing transcript files (marked finished but no file)
    if finished_but_no_file:
        missing_path = Path("missing_transcripts.txt")
        with open(missing_path, 'w', encoding='utf-8') as f:
            for url, vid_id in zip(finished_urls, finished_ids):
                if vid_id in finished_but_no_file:
                    f.write(f"{url}  # Video ID: {vid_id}, expected file: out/{vid_id}.json\n")
        print(f"⚠️  Created {missing_path} with {len(finished_but_no_file)} videos to re-transcribe")
    
    # Generate a report of orphaned transcripts
    if orphaned_transcripts:
        orphaned_path = Path("orphaned_transcripts.txt")
        with open(orphaned_path, 'w', encoding='utf-8') as f:
            for vid_id in sorted(orphaned_transcripts):
                f.write(f"{vid_id}  # File exists: out/{vid_id}.json, not tracked in finished.dat\n")
        print(f"📝 Created {orphaned_path} with {len(orphaned_transcripts)} untracked transcript files")
    
    print()
    print("=" * 80)
    print("Report complete!")
    print("=" * 80)


if __name__ == "__main__":
    main()


