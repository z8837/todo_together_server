# === concurrency setup ===
import os, threading, queue, uuid, time
from typing import Any,  Optional
from faster_whisper import WhisperModel
from typing import Optional
import tempfile
import subprocess
import numpy as np, subprocess

import re, unicodedata


from pathlib import Path
import json

from typing import Optional
_WHISPER_MODEL: Optional[WhisperModel] = None

def _get_whisper_model() -> WhisperModel:
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        _WHISPER_MODEL = WhisperModel(
            os.environ.get("WHISPER_MODEL", "small"),                 # tiny/base/small/medium/large-v3
            device=os.environ.get("WHISPER_DEVICE", "cpu"),           # "cuda" 사용 가능
            compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
            cpu_threads=int(os.environ.get("WHISPER_CPU_THREADS", "2")),
            num_workers=1,
        )
    return _WHISPER_MODEL


_BASE_DIR = Path(__file__).resolve().parents[2] / "django" / "youtube_meta"
_AUDIO_OUTPUT_DIR = _BASE_DIR / "audio"
_LOCAL_WAV_NAME = "250608.wav"

STT_MAX_CONCURRENCY = int(os.getenv("STT_MAX_CONCURRENCY", "2"))  # firebat T8 plus 권장 2
STT_MAX_QUEUE = int(os.getenv("STT_MAX_QUEUE", "50"))             # 큐 길이 상한 (가득 차면 429)

_STT_QUEUE: queue.Queue = queue.Queue(maxsize=STT_MAX_QUEUE)

class _STTJob:
    def __init__(self, payload: dict):
        self.id = str(uuid.uuid4())
        self.payload = payload
        self.done = threading.Event()
        self.result = None
        self.error = None

def _stt_worker():
    while True:
        job: _STTJob = _STT_QUEUE.get()
        try:
            job.result = _process_single_stt_request(job.payload)  # ↓ 2)에서 정의
        except Exception as e:
            job.error = f"{type(e).__name__}: {e}"
        finally:
            job.done.set()
            _STT_QUEUE.task_done()

# 워커 2개 기동(프로세스 당 1번)
for _ in range(STT_MAX_CONCURRENCY):
    t = threading.Thread(target=_stt_worker, daemon=True)
    t.start()







#로컬파일로 STT
def _probe_media_duration(path: Path) -> float:
    """ffprobe로 초 단위 길이(float) 반환. 실패 시 0."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        duration = float(json.loads(out.decode("utf-8"))["format"]["duration"])
        return max(0.0, duration)
    except Exception:
        return 0.0
    
def _make_wav_clip_from_file(src_wav_path: Path, start: float, duration: float, *, sr: int = 16000) -> str:
    """로컬 WAV에서 [start, start+duration)만 16k mono WAV로 추출하여 임시 경로 반환."""
    fd, out_path = tempfile.mkstemp(suffix=".wav"); os.close(fd)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(max(0.0, float(start))),
        "-i", str(src_wav_path),
        "-t", str(max(0.01, float(duration))),
        "-vn", "-ac", "1", "-ar", str(sr),
        "-f", "wav", out_path,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return out_path
    except subprocess.CalledProcessError as e:
        try: os.remove(out_path)
        except Exception: pass
        raise RuntimeError(f"ffmpeg failed: {e.stderr.decode(errors='ignore')[:400]}")


def _transcribe_audio_ranges_from_file(src_wav_path: Path, ranges, *, language: Optional[str] = None):
    all_segments = []
    lang = None
    for (s, e) in ranges:
        dur = max(0.01, e - s)
        res = transcribe_range_block(str(src_wav_path), s, dur, language=language or "ko")
        lang = lang or res.get("language")
        for seg in res.get("segments", []):
            all_segments.append({
                "start": round(s + seg["start"], 3),
                "end":   round(s + seg["end"], 3),
                "text":  seg["text"],
            })
    return {"language": lang, "segments": all_segments}




#로컬파일로 스트리밍 제너레이터
SR = 16000

def read_pcm_from_file_block(src_path: str, start: float, duration: float) -> np.ndarray:
    """ffmpeg로 [start, start+duration) 구간을 s16le로 파이프 읽기 → float32(-1~1) numpy"""
    start = max(0.0, float(start))
    duration = max(0.01, float(duration))

    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error",
        "-ss", str(start), "-i", src_path, "-t", str(duration),
        "-vn", "-sn", "-dn",
        "-ac", "1", "-ar", str(SR),
        "-f", "s16le", "pipe:1",
    ]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    raw = p.stdout.read()  # 예상 바이트: duration * SR * 2
    p.stdout.close()
    rc = p.wait()

    if rc != 0 or not raw:
        return np.zeros(0, dtype=np.float32)

    pcm_i16 = np.frombuffer(raw, dtype=np.int16)
    pcm_f32 = (pcm_i16.astype(np.float32) / 32768.0).copy()
    return pcm_f32

def transcribe_range_block(src_path: str, start: float, duration: float, *, language="ko"):
    model = _get_whisper_model()
    pcm = read_pcm_from_file_block(src_path, start, duration)
    if pcm.size == 0:
        return {"language": None, "segments": []}

    segments, info = model.transcribe(
        pcm,                   # ★ numpy float32 1ch 16kHz
        language=language,
        vad_filter=True,
        beam_size=5,
        word_timestamps=True,
    )
    out = []
    for seg in segments:
        out.append({
            "start": round(float(seg.start), 3),
            "end":   round(float(seg.end), 3),
            "text":  seg.text.strip(),
        })
    return {"language": getattr(info, "language", None), "segments": out}


def iter_pcm_from_file(src_path: str, start: float, duration: float, chunk_sec: float = 5.0):
    """
    ffmpeg로 s16le PCM을 파이프 읽기 → chunk 단위 float32 배열 yield.
    chunk_sec*SR*2 만큼씩 읽는다.
    """
    start = max(0.0, float(start))
    duration = max(0.01, float(duration))
    chunk_sec = max(0.05, float(chunk_sec))

    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error",
        "-ss", str(start), "-i", src_path, "-t", str(duration),
        "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(SR),
        "-f", "s16le", "pipe:1",
    ]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    bytes_per_sec = SR * 2          # 16-bit mono → 2 bytes/sample
    chunk_bytes   = int(bytes_per_sec * chunk_sec)

    try:
        while True:
            raw = p.stdout.read(chunk_bytes)
            if not raw:
                break
            pcm_i16 = np.frombuffer(raw, dtype=np.int16)
            if pcm_i16.size == 0:
                continue
            # copy()로 읽기 완료 후 버퍼 소유권 분리(메모리 안전)
            yield (pcm_i16.astype(np.float32) / 32768.0).copy()
    finally:
        p.stdout.close()
        p.wait()

def transcribe_range_stream(src_path: str, start: float, duration: float, *, language="ko"):
    model = _get_whisper_model()
    gen = iter_pcm_from_file(src_path, start, duration, chunk_sec=5.0)

    segments, info = model.transcribe(
        gen,                     # ★ generator of float32 chunks
        language=language,
        vad_filter=True,
        beam_size=5,
        word_timestamps=True,
    )
    out = []
    for seg in segments:
        out.append({
            "start": round(float(seg.start), 3),
            "end":   round(float(seg.end), 3),
            "text":  seg.text.strip(),
        })
    return {"language": getattr(info, "language", None), "segments": out}


# 실제 STT 처리(워커가 호출)
def _process_single_stt_request(payload: dict) -> dict:
    # 입력 파싱
    start = float(payload.get("start", 0.0))
    end = float(payload.get("end", 0.0))
    target_text = (payload.get("target_text") or "").strip()

    if not target_text:
        return {"error": "target_text is required."}

    # 로컬 오디오 파일 경로
    file_name = (payload.get("file_name") or "").strip() + ".wav"
    src_wav = _AUDIO_OUTPUT_DIR / file_name
    if not src_wav.exists():
        return {"error": f"audio file not found: {src_wav}"}

    # 길이 확보 (payload에 있으면 사용, 없으면 ffprobe)
    duration_seconds = float(payload.get("duration_seconds") or 0) or _probe_media_duration(src_wav)
    if duration_seconds <= 0:
        return {"error": "invalid_duration", "detail": "Could not determine audio duration."}

    # 최대 60초 캡
    if end <= start:
        end = start + 60.0
    else:
        end = min(end, start + 60.0)

    # 범위 정규화
    stt_ranges_raw = [{"start": start, "end": end}]
    ranges = _sanitize_ranges(stt_ranges_raw, duration_seconds)

    # STT
    try:
        if ranges:
            timeline = _transcribe_audio_ranges_from_file(src_wav, ranges, language="ko")
        else:
            # 전체에서 STT
            clip_all = _make_wav_clip_from_file(src_wav, 0.0, float(duration_seconds))
            try:
                timeline = _transcribe_wav(clip_all, language="ko")
            finally:
                try: os.remove(clip_all)
                except Exception: pass
    except Exception as exc:
        return {"error": f"transcription_failed: {str(exc)[:200]}"}

    # 매칭
    result = find_target_range_strict(timeline["segments"], target_text, threshold=0.6, coverage=0.95)
    return {"timeline": result}


def _sanitize_ranges(raw: Any, duration: Optional[int]) :
    """입력 구간을 검증/정규화(start<end, 0<=, duration 이내)."""
    out = []
    if not isinstance(raw, list):
        return out
    for r in raw:
        if not isinstance(r, dict): continue
        try:
            s = float(r.get("start", 0.0)); e = float(r.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if duration is not None:
            s = max(0.0, min(s, float(duration)))
            e = max(0.0, min(e, float(duration)))
        if e - s >= 0.25:   # 최소 길이(250ms) 필터
            out.append((s, e))
    # 겹치는 구간 정리(선택): 정렬 후 병합 or 그대로 진행
    out.sort(key=lambda x: x[0])
    return out



def _transcribe_wav(wav_path, *, language: Optional[str] = None):
    model = _get_whisper_model()
    segments, info = model.transcribe(
        wav_path,
        language="ko",            # "ko"로 고정하면 속도/정확도 ↑
        vad_filter=True,
        beam_size=5,
        word_timestamps=True,
    )
    out = []
    for seg in segments:
        out.append({
            "start": round(float(seg.start), 3),
            "end":   round(float(seg.end), 3),
            "text":  seg.text.strip(),
        })
    return {"language": getattr(info, "language", None), "segments": out}



    








try:
    from rapidfuzz import fuzz
    HAVE_RF = True
except Exception:
    from difflib import SequenceMatcher
    HAVE_RF = False

_PUNCT_RE = re.compile(r"[^\w가-힣]+")


def _normalize_ko(s: str) -> str:
    # 유니코드 정규화 + 소문자 + 불필요 기호/공백 제거
    s = unicodedata.normalize("NFKC", s)
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = " ".join(s.split())
    return s

def _normalize_with_map(s: str):
    """정규화된 문자열과, 정규화 문자 -> 원문 인덱스 매핑 리턴"""
    s = unicodedata.normalize("NFKC", s).lower()
    norm_chars, idx_map = [], []
    for i, ch in enumerate(s):
        if ch.isspace(): 
            continue
        if _PUNCT_RE.match(ch): 
            continue
        norm_chars.append(ch)
        idx_map.append(i)
    return "".join(norm_chars), idx_map

def similarity_score(a: str, b: str) -> float:
    a_n = _normalize_ko(a or "")
    b_n = _normalize_ko(b or "")
    if HAVE_RF:
        # 토큰 기반: 순서/중복/불필요 단어에 강함
        return fuzz.token_set_ratio(a_n, b_n) / 100.0
    else:
        return SequenceMatcher(None, a_n, b_n).ratio()
    
def find_target_range_strict(segments, target_text: str,
                             threshold: float = 0.60, coverage: float = 1.00):
    """
    누적 텍스트를 세그먼트 순서대로 붙여가며,
    '전체 타깃 길이'를 채운 뒤 엄격 비교로 임계 도달 시 거기까지 타임라인 반환.
    """
    collected = ""
    for idx, seg in enumerate(segments):
        collected = (collected + " " + seg["text"]).strip() if collected else seg["text"]
        stop, sim, end_char_idx = should_stop_on_full_target(
            collected, target_text, threshold=threshold, coverage=coverage
        )
        if stop or idx == len(segments) - 1:
            used = segments[:idx+1]
            trimmed_segments, trimmed_text = trim_segments_to_char_index(used, end_char_idx)
            return {
                "start": trimmed_segments[0]["start"],
                "end":   trimmed_segments[-1]["end"],
                "matched_text": trimmed_text,
                "similarity": round(sim, 4),
            }
    return {
        "start": 0.0,
        "end" : 0.0,
        "matched_text" : collected,
        "similarity" : 0.0
    }

def trim_segments_to_char_index(segments, end_char_idx: int):
    """full_text 기준 end_char_idx(원문 인덱스, inclusive)까지만 세그먼트 자르기"""
    # full_text와 세그먼트 경계 추정 (간단히 " ".join 모델)
    full = " ".join(s["text"] for s in segments).strip()
    if end_char_idx is None or end_char_idx >= len(full):
        return segments, full

    out, cursor = [], 0
    for i, seg in enumerate(segments):
        sep = 1 if (out or cursor > 0) else 0
        seg_start = cursor + (1 if sep else 0)
        seg_end   = seg_start + len(seg["text"]) - 1

        if end_char_idx < seg_start:
            break
        if end_char_idx >= seg_end:
            out.append(dict(seg))
        else:
            keep_len = end_char_idx - seg_start + 1
            new_seg = dict(seg)
            new_seg["text"] = seg["text"][:keep_len].rstrip()
            # 문자 비율로 end 근사 (word_ts 없을 때)
            ratio = max(0.0, min(1.0, keep_len / max(1, len(seg["text"]))))
            new_seg["end"] = seg["start"] + (seg["end"] - seg["start"]) * ratio
            out.append(new_seg)
            break
        cursor = seg_end
    trimmed_text = " ".join(s["text"] for s in out).strip()
    return out, trimmed_text


# --- 판정: '전체 대상'으로만 비교 ---
def should_stop_on_full_target(full_text: str, target_text: str, *, 
                               threshold: float = 0.60, 
                               coverage: float = 1.00):
    """
    누적 full_text가 target_text와 '전체 길이 기준'으로 충분히 일치하면
    (stop=True, sim, end_char_idx) 반환. 아니면 (False, sim, None).
      - threshold: 유사도 임계 (0~1)
      - coverage : 최소 커버리지(정규화 길이 비율). 1.0=target 길이 이상 모였을 때만 평가
    """
    n_full, map_full = _normalize_with_map(full_text or "")
    n_tgt, _        = _normalize_with_map(target_text or "")

    if not n_tgt:
        return False, 0.0, None

    # 1) 길이 커버리지 게이트
    if len(n_full) < len(n_tgt) * coverage:
        return False, 0.0, None

    # 2) 앞부분을 target 길이에 맞춰 '정면 비교'
    n_full_clip = n_full[:len(n_tgt)]
    if HAVE_RF:
        sim = fuzz.ratio(n_full_clip, n_tgt) / 100.0    # 엄격 비교
    else:
        sim = SequenceMatcher(None, n_full_clip, n_tgt).ratio()

    if sim >= threshold:
        # 정규화 끝 인덱스를 원문 인덱스로 역매핑 (포함 index)
        end_norm_idx = len(n_tgt) - 1
        end_char_idx = map_full[end_norm_idx] if end_norm_idx < len(map_full) else None
        return True, sim, end_char_idx

    return False, sim, None