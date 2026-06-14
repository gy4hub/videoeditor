#!/usr/bin/env python3
"""
transcribe.py — 用 faster-whisper 对音/视频文件做词级时间戳转写，输出 JSON。

Usage:
    python3 transcribe.py <audio_or_video> [options]
    python3 transcribe.py --help

Output JSON schema:
    {
      "source": "<filename>",
      "model": "<model_name>",
      "language": "zh",
      "language_probability": 0.999,
      "duration_s": 76.5,
      "transcribe_time_s": 5.6,
      "words": [
        {"word": "欢", "start": 0.0, "end": 0.4, "confidence": 0.827},
        ...
      ],
      "segments": [
        {"start": 0.0, "end": 4.5, "text": "欢迎来到..."},
        ...
      ]
    }
"""

import argparse
import json
import os
import sys
import time


def load_model(model_name_or_path: str, device: str = "cpu", compute_type: str = "int8"):
    """Load faster-whisper model. Accepts HF model name or local directory path."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("ERROR: faster-whisper not installed. Run: pip install faster-whisper --break-system-packages", file=sys.stderr)
        sys.exit(1)

    print(f"[transcribe] Loading model: {model_name_or_path} (device={device}, compute_type={compute_type})", file=sys.stderr)
    return WhisperModel(model_name_or_path, device=device, compute_type=compute_type)


def transcribe(
    audio_path: str,
    model_name_or_path: str = "tiny",
    language: str = "zh",
    device: str = "cpu",
    compute_type: str = "int8",
    vad: bool = True,
    vad_min_silence_ms: int = 300,
) -> dict:
    """
    Transcribe audio/video file, returning a dict with word-level timestamps.

    Parameters
    ----------
    audio_path : str
        Path to audio or video file (any format ffmpeg can decode).
    model_name_or_path : str
        HF model name (e.g. "tiny", "small", "medium", "large-v3") or local path.
    language : str
        Language code (e.g. "zh", "en"). None = auto-detect.
    device : str
        "cpu" or "cuda".
    compute_type : str
        "int8" (CPU default), "float16" (GPU), "float32".
    vad : bool
        Whether to apply Voice Activity Detection filter.
    vad_min_silence_ms : int
        Minimum silence duration in ms for VAD splitting.

    Returns
    -------
    dict with keys: source, model, language, language_probability,
                    duration_s, transcribe_time_s, words, segments
    """
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    model = load_model(model_name_or_path, device=device, compute_type=compute_type)

    vad_params = dict(min_silence_duration_ms=vad_min_silence_ms) if vad else {}

    print(f"[transcribe] Transcribing: {audio_path}", file=sys.stderr)
    t0 = time.time()
    segments_iter, info = model.transcribe(
        audio_path,
        language=language if language else None,
        word_timestamps=True,
        vad_filter=vad,
        vad_parameters=vad_params if vad else None,
    )

    words_data = []
    seg_list = []
    for seg in segments_iter:
        seg_list.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
        })
        if seg.words:
            for w in seg.words:
                words_data.append({
                    "word": w.word,
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "confidence": round(w.probability, 4),
                })

    elapsed = round(time.time() - t0, 2)
    print(f"[transcribe] Done in {elapsed}s — {len(words_data)} words, {len(seg_list)} segments", file=sys.stderr)

    # Get audio duration via ffprobe if possible
    duration_s = _get_duration(audio_path)

    return {
        "source": os.path.basename(audio_path),
        "model": model_name_or_path,
        "language": info.language,
        "language_probability": round(info.language_probability, 4),
        "duration_s": duration_s,
        "transcribe_time_s": elapsed,
        "words": words_data,
        "segments": seg_list,
    }


def _get_duration(path: str) -> float:
    """Return audio duration in seconds via ffprobe, or -1 on failure."""
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10
        )
        return round(float(result.stdout.strip()), 3)
    except Exception:
        return -1.0


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe audio/video to word-level timestamp JSON using faster-whisper.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("audio", help="Path to audio or video file")
    parser.add_argument(
        "-o", "--output",
        help="Output JSON path (default: <audio_basename>_transcript.json beside input file)",
        default=None,
    )
    parser.add_argument(
        "-m", "--model",
        help="Model name or local path (default: tiny). Production: large-v3.",
        default="tiny",
    )
    parser.add_argument(
        "-l", "--language",
        help="Language code (default: zh). Use 'auto' for detection.",
        default="zh",
    )
    parser.add_argument(
        "--device",
        help="Compute device: cpu or cuda (default: cpu)",
        default="cpu",
    )
    parser.add_argument(
        "--compute-type",
        help="Quantisation: int8 / float16 / float32 (default: int8)",
        default="int8",
    )
    parser.add_argument(
        "--no-vad",
        help="Disable Voice Activity Detection filter",
        action="store_true",
    )
    parser.add_argument(
        "--vad-silence-ms",
        help="VAD min silence duration in ms (default: 300)",
        type=int,
        default=300,
    )

    args = parser.parse_args()

    lang = None if args.language == "auto" else args.language

    result = transcribe(
        audio_path=args.audio,
        model_name_or_path=args.model,
        language=lang,
        device=args.device,
        compute_type=args.compute_type,
        vad=not args.no_vad,
        vad_min_silence_ms=args.vad_silence_ms,
    )

    if args.output:
        out_path = args.output
    else:
        base = os.path.splitext(args.audio)[0]
        out_path = base + "_transcript.json"

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[transcribe] Saved → {out_path}")
    print(f"  words: {len(result['words'])}, duration: {result['duration_s']}s, "
          f"RTF: {result['transcribe_time_s'] / max(result['duration_s'], 0.01):.3f}")


if __name__ == "__main__":
    main()
