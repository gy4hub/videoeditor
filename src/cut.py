#!/usr/bin/env python3
"""
cut.py — 用 ffmpeg 按时间码（或词级时间戳 JSON）切割音视频片段并拼接。

Usage:
    # 直接指定时间码
    python3 cut.py <input> -s 13.28 -e 14.48 -o output/seg1.wav
    python3 cut.py <input> -s 13.28 -e 14.48 -s 24.42 -e 25.88 -o output/concat.wav

    # 从转写 JSON 按词索引切割
    python3 cut.py <input> --transcript eval/s0-1_transcript.json --word-idx 43 47 -o output/seg.wav
    python3 cut.py <input> --transcript eval/s0-1_transcript.json \
            --word-idx 43 47 --word-idx 73 77 -o output/concat.wav

    python3 cut.py --help

Notes:
    - 多段切割时自动拼接为单一输出文件（concat demuxer）。
    - 单段时直接输出，不额外拼接。
    - 时间单位：秒（支持小数），或 HH:MM:SS.mmm 格式。
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile


def _tc_to_sec(tc: str) -> float:
    """Convert HH:MM:SS.mmm or plain float string to seconds."""
    tc = tc.strip()
    if ":" in tc:
        parts = tc.split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        elif len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
    return float(tc)


def cut_segment(
    input_path: str,
    start: float,
    end: float,
    output_path: str,
    copy_codec: bool = False,
) -> str:
    """
    Cut a single segment [start, end) from input_path to output_path.

    Parameters
    ----------
    input_path : str
        Source audio/video file.
    start, end : float
        Segment boundaries in seconds.
    output_path : str
        Destination file path.
    copy_codec : bool
        If True, use stream copy (fast, may have keyframe imprecision).
        If False (default), re-encode for sample-accurate cuts.

    Returns
    -------
    str — output_path on success.
    """
    if end <= start:
        raise ValueError(f"end ({end}) must be > start ({start})")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    cmd = ["ffmpeg", "-y"]
    if copy_codec:
        cmd += ["-ss", str(start), "-to", str(end), "-i", input_path, "-c", "copy"]
    else:
        cmd += ["-ss", str(start), "-to", str(end), "-i", input_path]
    cmd.append(output_path)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg cut failed (exit {result.returncode}):\n{result.stderr[-1000:]}"
        )
    return output_path


def concat_segments(segment_paths: list, output_path: str) -> str:
    """
    Concatenate a list of audio/video files into output_path using ffmpeg concat demuxer.

    Parameters
    ----------
    segment_paths : list[str]
        Ordered list of segment files (must all have same codec/sample rate).
    output_path : str
        Destination file path.

    Returns
    -------
    str — output_path on success.
    """
    if not segment_paths:
        raise ValueError("No segments to concatenate")
    if len(segment_paths) == 1:
        import shutil
        shutil.copy2(segment_paths[0], output_path)
        return output_path

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for p in segment_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
        list_path = f.name

    try:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
               "-i", list_path, "-c", "copy", output_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg concat failed (exit {result.returncode}):\n{result.stderr[-1000:]}"
            )
    finally:
        os.unlink(list_path)

    return output_path


def cut_and_concat(
    input_path: str,
    intervals: list,
    output_path: str,
    copy_codec: bool = False,
    keep_intermediates: bool = False,
) -> str:
    """
    Cut multiple intervals from input_path and concatenate to output_path.

    Parameters
    ----------
    input_path : str
        Source file.
    intervals : list of (start, end) tuples in seconds.
    output_path : str
        Final concatenated output.
    copy_codec : bool
        See cut_segment().
    keep_intermediates : bool
        If True, leave temp segment files on disk (named <output>_part_N).

    Returns
    -------
    str — output_path on success.
    """
    if not intervals:
        raise ValueError("No intervals provided")

    if len(intervals) == 1:
        return cut_segment(input_path, intervals[0][0], intervals[0][1],
                           output_path, copy_codec=copy_codec)

    base, ext = os.path.splitext(output_path)
    tmp_paths = []
    for i, (s, e) in enumerate(intervals):
        tmp_path = f"{base}_part_{i:03d}{ext}"
        cut_segment(input_path, s, e, tmp_path, copy_codec=copy_codec)
        tmp_paths.append(tmp_path)
        print(f"[cut] Segment {i+1}/{len(intervals)}: {s:.3f}s → {e:.3f}s → {tmp_path}", file=sys.stderr)

    concat_segments(tmp_paths, output_path)
    print(f"[cut] Concatenated {len(tmp_paths)} segments → {output_path}", file=sys.stderr)

    if not keep_intermediates:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    return output_path


def intervals_from_word_indices(transcript: dict, word_ranges: list) -> list:
    """
    Given a transcript dict (from transcribe.py) and a list of (start_idx, end_idx) pairs,
    return corresponding (start_sec, end_sec) intervals.

    Parameters
    ----------
    transcript : dict
        Loaded JSON from transcribe.py — must have "words" list.
    word_ranges : list of (int, int)
        Each tuple: (first_word_index, last_word_index) inclusive.

    Returns
    -------
    list of (float, float) tuples.
    """
    words = transcript["words"]
    intervals = []
    for (wi_start, wi_end) in word_ranges:
        if wi_start < 0 or wi_end >= len(words):
            raise IndexError(
                f"Word index out of range: [{wi_start}, {wi_end}] "
                f"(transcript has {len(words)} words)"
            )
        s = words[wi_start]["start"]
        e = words[wi_end]["end"]
        intervals.append((s, e))
    return intervals


def main():
    parser = argparse.ArgumentParser(
        description="Cut and concatenate audio/video segments by timecode or word index.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", help="Source audio or video file")
    parser.add_argument("-o", "--output", required=True, help="Output file path")

    # Timecode-based cutting
    tc_group = parser.add_argument_group("Timecode mode (direct start/end in seconds or HH:MM:SS)")
    tc_group.add_argument(
        "-s", "--start",
        action="append", dest="starts", metavar="SEC",
        help="Start time (seconds or HH:MM:SS.mmm). Repeat for multiple segments.",
    )
    tc_group.add_argument(
        "-e", "--end",
        action="append", dest="ends", metavar="SEC",
        help="End time. Must be paired with each -s.",
    )

    # Word-index-based cutting
    wi_group = parser.add_argument_group("Word-index mode (requires --transcript)")
    wi_group.add_argument(
        "--transcript",
        help="Path to transcript JSON from transcribe.py",
        default=None,
    )
    wi_group.add_argument(
        "--word-idx",
        nargs=2, type=int, metavar=("START_IDX", "END_IDX"),
        action="append", dest="word_ranges",
        help="Word index range [START_IDX END_IDX] inclusive. Repeat for multiple segments.",
    )

    parser.add_argument(
        "--copy", action="store_true",
        help="Use stream copy (fast but may be keyframe-imprecise for video)",
    )
    parser.add_argument(
        "--keep-parts", action="store_true",
        help="Keep intermediate segment files when concatenating",
    )

    args = parser.parse_args()

    # Determine intervals
    intervals = []

    if args.word_ranges:
        if not args.transcript:
            parser.error("--word-idx requires --transcript")
        with open(args.transcript, encoding="utf-8") as f:
            transcript = json.load(f)
        intervals = intervals_from_word_indices(transcript, args.word_ranges)
        print(f"[cut] Word-index intervals: {intervals}", file=sys.stderr)

    elif args.starts:
        if not args.ends or len(args.starts) != len(args.ends):
            parser.error("Each -s/--start must have a matching -e/--end")
        for s, e in zip(args.starts, args.ends):
            intervals.append((_tc_to_sec(s), _tc_to_sec(e)))

    else:
        parser.error("Provide either (-s/-e) timecodes or (--transcript + --word-idx)")

    if not intervals:
        parser.error("No intervals resolved — nothing to cut")

    out = cut_and_concat(
        input_path=args.input,
        intervals=intervals,
        output_path=args.output,
        copy_codec=args.copy,
        keep_intermediates=args.keep_parts,
    )

    # Report output duration
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", out],
            capture_output=True, text=True, timeout=10
        )
        dur = float(result.stdout.strip())
        print(f"[cut] Output: {out} ({dur:.3f}s)")
    except Exception:
        print(f"[cut] Output: {out}")


if __name__ == "__main__":
    main()
