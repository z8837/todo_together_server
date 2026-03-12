from django.http import HttpResponse, JsonResponse
from django.core.serializers.json import DjangoJSONEncoder
import json
import os
from wakeonlan import send_magic_packet
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponseForbidden


from pathlib import Path
import os, re, json, tempfile, subprocess
from typing import Optional
from django.http import HttpRequest, HttpResponse, JsonResponse


import subprocess, shlex, tempfile

import sys, subprocess, shlex

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
from typing import Any, Dict, Iterable, List, Optional, Sequence
import datetime

from .models import Log
from django.utils import timezone

def log(request, level, content1, content2, content3, content4, content5, content6, content7):
	log = Log(level=level, account=content1, 
	authentication=content2, group = content3, ip = get_client_ip(request), 
    platform=content4, version=content5,
    category=content6, content=content7, date=timezone.now() )
	log.save()

def get_client_ip(request):
	#request.META['REMOTE_ADDR'] = request.META['HTTP_X_REAL_IP']
	x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
	if x_forwarded_for:
		ip = x_forwarded_for.split(',')[-1]
	else:
		ip = request.META.get('REMOTE_ADDR')
	return ip

API_TOKEN = os.environ.get("WOL_API_TOKEN", "")  # 환경변수로 관리

@csrf_exempt
@require_POST
def hello3(request):
    # 간단 토큰 인증
    token = request.headers.get("X-API-TOKEN") or request.POST.get("token")
    if not API_TOKEN or token != API_TOKEN:
        return returnJson({"token" : token})
        return HttpResponseForbidden("Forbidden")

    # MAC 주소 (필수)
    # mac = request.POST.get("mac")
    # 옵션: 특정 브로드캐스트 IP나 포트로 보낼 수도 있음
    bcast = request.POST.get("broadcast", "255.255.255.255")
    port = int(request.POST.get("port", 9))
    mac = "C8:7F:54:51:B6:A6"
    try:
        send_magic_packet(mac, ip_address=bcast, port=port)
        return JsonResponse({"ok": True, "mac": mac, "broadcast": bcast, "port": port})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

def hello2(request):
    return returnJson({"message": "안녕 운댕! 캬캬22 ^_^"})

def hello(request):
    return returnJson({"message": "안녕 운댕! 캬캬캬캬키캬쿄쿄22 ^_^"})

def returnJson(result, beautify=False):
	if beautify:# or settings.DEBUG:
		content = json.dumps(result, ensure_ascii=False, cls=DjangoJSONEncoder, indent=4)
	else:
		content = json.dumps(result, ensure_ascii=False, cls=DjangoJSONEncoder)
	response = HttpResponse(content, content_type="application/json; encoding=utf-8")
	response["Access-Control-Allow-Origin"] = "*"
	# response["Access-Control-Allow-Origin"] = "http://localhost:3000"
	return response
















#유튜브 MetaData

def _best_thumbnail(info: Dict[str, Any]) -> Optional[str]:
    thumbnails = info.get("thumbnails") or []
    if thumbnails:
        best = max(
            (thumb for thumb in thumbnails if thumb.get("url")),
            key=lambda thumb: thumb.get("height") or 0,
            default=None,
        )
        if best:
            return best.get("url")
    return info.get("thumbnail")


def _score_tuple(fmt: Dict[str, Any]) -> tuple[int, float, float]:
    # (height, fps, bitrate) 순으로 정렬 점수
    return int(fmt.get("height") or 0), float(fmt.get("fps") or 0), float(fmt.get("tbr") or 0)


def _best_muxed_format(formats: Optional[Iterable[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    if not formats:
        return None
    muxed = [
        f for f in formats
        if f.get("url") and f.get("vcodec") != "none" and f.get("acodec") != "none"
    ]
    candidates = muxed or list(formats)
    return max(candidates, key=_score_tuple, default=None)


def _best_video_only(formats: Optional[Iterable[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    if not formats:
        return None
    videos = [f for f in formats if f.get("vcodec") != "none"]
    if not videos:
        return None
    # 오디오 없는(=video-only) 것을 우선시, 그다음 고화질/고FPS/고비트레이트
    videos.sort(
        key=lambda f: (
            f.get("acodec") == "none",
            *_score_tuple(f),
        ),
        reverse=True,
    )
    return videos[0]


def _best_audio_only(formats: Optional[Iterable[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    if not formats:
        return None
    audios = [f for f in formats if f.get("acodec") != "none" and f.get("vcodec") == "none"]
    if not audios:
        return None
    audios.sort(key=lambda f: (float(f.get("abr") or 0), float(f.get("tbr") or 0)), reverse=True)
    return audios[0]


def _normalize_duration(raw: Any) -> tuple[Optional[int], Optional[str]]:
    if isinstance(raw, (int, float)):
        seconds = max(int(raw), 0)
        return seconds, str(datetime.timedelta(seconds=seconds))
    return None, None


def _fmt_quality(fmt: Dict[str, Any]) -> Optional[str]:
    if not fmt:
        return None
    return (
        fmt.get("format_note")
        or fmt.get("resolution")
        or (f"{fmt.get('height')}p" if fmt.get("height") else None)
    )


def _is_progressive_http(fmt: Dict[str, Any]) -> bool:
    """오디오+비디오 포함 & HTTP 직결만 True (HLS/DASH/manifest 제외)"""
    if not fmt or not fmt.get("url"):
        return False
    if fmt.get("vcodec") == "none" or fmt.get("acodec") == "none":
        return False
    proto = (fmt.get("protocol") or "").lower()
    if proto in {"m3u8", "m3u8_native", "http_dash_segments", "dash", "ism"}:
        return False
    url = fmt.get("url", "")
    if "manifest.googlevideo.com" in url or ".m3u8" in url:
        return False
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    # (선택) 확장자 힌트
    ext = (fmt.get("ext") or "").lower()
    if ext and ext not in {"mp4", "webm"}:
        return False
    return True

def _pick_origin_muxed(formats: Optional[Iterable[Dict[str, Any]]], target_height: int = 360) -> Optional[Dict[str, Any]]:
    """진행형 HTTP 직결 중 target_height 우선, 없으면 가장 가까운 해상도"""
    if not formats:
        return None
    muxed = [f for f in formats if _is_progressive_http(f)]
    if not muxed:
        return None

    exact = [f for f in muxed if int(f.get("height") or 0) == target_height]
    if exact:
        exact.sort(key=_score_tuple, reverse=True)
        return exact[0]

    def score(fmt: Dict[str, Any]) -> tuple[int, float, float, int]:
        h = int(fmt.get("height") or 0)
        return (abs(h - target_height), -(fmt.get("fps") or 0.0), -(fmt.get("tbr") or 0.0), h)
    return min(muxed, key=score, default=None)


@csrf_exempt
@require_POST
def fetch_metadata(request: HttpRequest) -> JsonResponse:
    # 1) 입력 파싱
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    url = payload.get("url") if isinstance(payload, dict) else None
    if not url or not isinstance(url, str):
        return JsonResponse({"error": 'A non-empty "url" string is required.'}, status=400)

    # 2) yt-dlp 호출 (다운로드 없이 포맷 선정만)
    ydl_opts = {
        "format": "bestvideo+bestaudio/best",  # 분리 스트림 우선, 여의치 않으면 best 진행형
        "skip_download": True,
        "noplaylist": True,
        "quiet": True,
        "ignoreerrors": False,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        return JsonResponse(
            {"error": "Failed to retrieve metadata from YouTube.", "details": str(exc).splitlines()[0]},
            status=400,
        )
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"error": "Unexpected error while fetching metadata.", "details": str(exc)}, status=500)

    # 3) 길이/썸네일
    duration_seconds, duration_display = _normalize_duration(info.get("duration"))
    thumbnail_url = _best_thumbnail(info)

    # 4) 스트림 정보 구성 (requested_formats 우선)
    stream_data: Dict[str, Any] = {
        "separate_av": None,             # True(분리) / False(진행형) / None(미확정)
        "strategy": None,                # "requested" | "split_pick" | "muxed_fallback" | "info_fallback"
        "quality": None,
        "fps": None,
    }

    requested = info.get("requested_formats") or info.get("requested_downloads")
    if requested:
        # 보통 [video, audio] 두 개. 가끔 하나만 있을 수도 있음(진행형).
        video_fmt = next((f for f in requested if f.get("vcodec") != "none"), None)
        audio_fmt = next((f for f in requested if f.get("acodec") != "none" and f.get("vcodec") == "none"), None)

        if video_fmt and audio_fmt:
            stream_data.update(
                {
                    "separate_av": True,
                    "strategy": "requested",
                    "video_url": video_fmt.get("url"),
                    "audio_url": audio_fmt.get("url"),
                    "video_mime_type": video_fmt.get("mime_type"),
                    "audio_mime_type": audio_fmt.get("mime_type"),
                    "filesize_bytes_video": video_fmt.get("filesize") or video_fmt.get("filesize_approx"),
                    "filesize_bytes_audio": audio_fmt.get("filesize") or audio_fmt.get("filesize_approx"),
                    "quality": _fmt_quality(video_fmt),
                    "fps": video_fmt.get("fps"),
                }
            )
        elif video_fmt:
            # 드물게 영상만 선택되는 경우
            stream_data.update(
                {
                    "separate_av": True,
                    "strategy": "requested",
                    "video_url": video_fmt.get("url"),
                    "audio_url": None,
                    "video_mime_type": video_fmt.get("mime_type"),
                    "audio_mime_type": None,
                    "filesize_bytes_video": video_fmt.get("filesize") or video_fmt.get("filesize_approx"),
                    "filesize_bytes_audio": None,
                    "quality": _fmt_quality(video_fmt),
                    "fps": video_fmt.get("fps"),
                }
            )
        else:
            # 요청 포맷이 하나인데 그게 진행형일 수도 있음
            only = requested[0]
            stream_data.update(
                {
                    "separate_av": False,
                    "strategy": "requested",
                    "url": only.get("url"),
                    "mime_type": only.get("mime_type"),
                    "filesize_bytes": only.get("filesize") or only.get("filesize_approx"),
                    "quality": _fmt_quality(only),
                    "fps": only.get("fps"),
                }
            )
    else:
        # 5) requested_formats가 없으면 직접 선별
        formats = info.get("formats") or []
        best_video = _best_video_only(formats)
        best_audio = _best_audio_only(formats)

        if best_video and best_audio:
            stream_data.update(
                {
                    "separate_av": True,
                    "strategy": "split_pick",
                    "video_url": best_video.get("url"),
                    "audio_url": best_audio.get("url"),
                    "video_mime_type": best_video.get("mime_type"),
                    "audio_mime_type": best_audio.get("mime_type"),
                    "filesize_bytes_video": best_video.get("filesize") or best_video.get("filesize_approx"),
                    "filesize_bytes_audio": best_audio.get("filesize") or best_audio.get("filesize_approx"),
                    "quality": _fmt_quality(best_video),
                    "fps": best_video.get("fps"),
                }
            )
        else:
            best_muxed = _best_muxed_format(formats)
            if best_muxed:
                stream_data.update(
                    {
                        "separate_av": False,
                        "strategy": "muxed_fallback",
                        "url": best_muxed.get("url"),
                        "mime_type": best_muxed.get("mime_type"),
                        "filesize_bytes": best_muxed.get("filesize") or best_muxed.get("filesize_approx"),
                        "quality": _fmt_quality(best_muxed),
                        "fps": best_muxed.get("fps"),
                    }
                )
            else:
                # 최후의 보루
                stream_data.update(
                    {
                        "separate_av": False,
                        "strategy": "info_fallback",
                        "url": info.get("url"),
                        "mime_type": info.get("format"),
                        "filesize_bytes": None,
                        "quality": info.get("format"),
                        "fps": None,
                    }
                )

    # ⬇⬇⬇ 추가: origin_stream 구성 (가능하면 360p 진행형)
    origin_fmt = _pick_origin_muxed(info.get("formats") or [], target_height=360)
    origin_stream = None
    if origin_fmt:
        origin_stream = {
            "url": origin_fmt.get("url"),
            "quality": _fmt_quality(origin_fmt),
            "mime_type": origin_fmt.get("mime_type"),
            "filesize_bytes": origin_fmt.get("filesize") or origin_fmt.get("filesize_approx"),
            "fps": origin_fmt.get("fps"),
        }
    elif info.get("url"):
        # 아주 드문 케이스: formats가 비어있고 info.url만 있는 경우
        origin_stream = {
            "url": info.get("url"),
            "quality": info.get("format"),
            "mime_type": None,
            "filesize_bytes": None,
            "fps": None,
        }
    # ⬆⬆⬆ 추가 끝

    log(request, 1, '유튜브 데이터 불러오기', '', '', '','','',str(info.get("title") or ""))
    # 6) 응답 조립
    response_payload = {
        "id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("uploader"),
        "thumbnail_url": thumbnail_url,
        "duration_seconds": duration_seconds,
        "duration_display": duration_display,
        "best_stream": stream_data,
        "origin_stream": origin_stream,  # ⬅️ 추가
        "webpage_url": info.get("webpage_url"),
    }
    return JsonResponse(response_payload)
    












#유튜브 다운로드
# ---- 출력 디렉토리 (원하는 경로로 바꿔도 됩니다)
BASE_DIR = Path(__file__).resolve().parents[2] / "django" / "youtube_meta"
VIDEO_DIR = BASE_DIR / "video"
AUDIO_DIR = BASE_DIR / "audio"
ANALYSIS_DIR = (BASE_DIR / "analysis")

VIDEO_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")

def _sanitize_filename(v: Optional[str]) -> str:
    base = (v or "").strip() or "auto_part_clip"
    s = _SAFE_FILENAME_PATTERN.sub("_", base).strip("._")
    return s or "auto_part_clip"

def _ensure_mp4_simple(url: str, dest_mp4: Path) -> None:
    import yt_dlp
    outtmpl_base = str(dest_mp4.with_suffix(""))
    ydl_opts = {
        "outtmpl": outtmpl_base,
        "format": 'bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/best[ext=mp4]/best',
        "merge_output_format": "mp4",
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        "noplaylist": True,
        # CLI와 최대한 동일하게: (불필요한 커스텀 옵션 제거)
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)
    # 결과 파일명 보정
    if not dest_mp4.exists():
        for c in dest_mp4.parent.glob(dest_mp4.stem + ".*"):
            if c.suffix.lower() == ".mp4":
                c.replace(dest_mp4)
                break

def _run_cli_same_as_shell(url: str, dest_mp4: Path) -> tuple[bool, str]:
    outtmpl = str(dest_mp4.with_suffix("")) + ".%(ext)s"
    env = os.environ.copy()  # ← 프록시/FFMPEG 등 환경 유지
    cmd = [
        sys.executable, "-m", "yt_dlp", "-v",
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", outtmpl,
        url,
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    ok = p.returncode == 0 and any(dest_mp4.parent.glob(dest_mp4.stem + ".*"))
    log = (p.stdout + "\n" + p.stderr)
    return ok, log[-8000:]  # 꼬리 넉넉히

class VideoBuildError(Exception):
    """Raised when auto-part video assembly fails."""

def _run_ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg"] + args
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True
        )
    except subprocess.CalledProcessError as e:
        raise VideoBuildError(e.stderr.decode("utf-8", "ignore")[:500])

def _extract_wav_from_mp4(src_mp4: Path, dest_wav: Path, sr: int = 16000) -> bool:
    """
    mp4에서 16k 모노 WAV 추출. 이미 있으면 False, 생성하면 True.
    """
    if dest_wav.exists():
        return False
    dest_wav.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="yt_wav_") as td:
        tmp_out = Path(td) / "out.wav"
        _run_ffmpeg([
            "-y", "-loglevel", "error",
            "-i", str(src_mp4),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(sr),
            "-ac", "1",
            str(tmp_out),
        ])
        tmp_out.replace(dest_wav)
    return True


import yt_dlp, re, tempfile
@csrf_exempt
# @require_GET
def download_youtube(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error":"Invalid JSON body."}, status=400)

    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        return JsonResponse({"error": 'A non-empty "url" string is required.'}, status=400)

    filename = payload.get("filename")
    if not filename:
        # 간단히 id기반 파일명
        m = re.search(r"v=([A-Za-z0-9_-]{6,})", url)
        filename = m.group(1) if m else "output"
    filename = _sanitize_filename(filename)

    mp4_path = VIDEO_DIR / f"{filename}.mp4"
    wav_path = AUDIO_DIR / f"{filename}.wav"

    # 1) 프록시 제거 테스트 (필요시 조정)
    # for k in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy"):
    #     os.environ.pop(k, None)
    
    
    debug = {}
    try:
        mp4_created = False
        cli = False
        if not mp4_path.exists():
            try:
                _ensure_mp4_simple(url, mp4_path)
                # _run_cli_same_as_shell(url, mp4_path)
                mp4_created = True
            except Exception as e1:
                debug["python_api_error"] = str(e1)[:1000]
                cli = True
                #2) CLI 폴백
                ok, log = _run_cli_same_as_shell(url, mp4_path)
                debug["cli_fallback_log"] = log
                if not ok:
                    return JsonResponse({
                        "error": "yt_dlp_failed_both",
                        "detail": debug
                    }, status=500)

        # 3) WAV 추출
        wav_created = False
        if mp4_path.exists() and not wav_path.exists():
            wav_created = _extract_wav_from_mp4(mp4_path, wav_path, sr=16000)

        return JsonResponse({
            "status": "ok",
            "use_cli" : cli,
            "filename": filename,
            "video_exists": mp4_path.exists(),
            "audio_exists": wav_path.exists(),
            "mp4_created": mp4_created,
            "wav_created": wav_created,
            "paths": {"mp4": str(mp4_path), "wav": str(wav_path)},
            "debug": debug  # 필요 시 확인
        })
    except Exception as e:
        return JsonResponse({"error": str(e), "debug": debug}, status=500)















#STT

from .queueing import _STT_QUEUE, _STTJob
@csrf_exempt
@require_POST
def stt(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    # 큐가 가득 차면 즉시 429
    if _STT_QUEUE.full():
        return JsonResponse({"error": "stt_queue_full", "queued": _STT_QUEUE.qsize()}, status=429)

    # 작업 등록
    job = _STTJob(payload)
    _STT_QUEUE.put(job)

    # 완료 대기 (타임아웃 옵션: 기본 600초)
    timeout = float(payload.get("timeout", 600.0))
    ok = job.done.wait(timeout=timeout)
    if not ok:
        return JsonResponse({"error": "stt_timeout", "job_id": job.id}, status=504)

    if job.error:
        return JsonResponse({"error": job.error, "job_id": job.id}, status=500)

    log(request, 2, 'STT', '', '', '','','',str(job.result.timeline.similarity or ""))
    # 정상 완료 → 결과 반환
    return JsonResponse(job.result or {"timeline": None, "job_id": job.id})






from typing import Any, Dict, List, Optional, Sequence, Tuple

#영상 이어 붙이기
# ----- 시간/파일 유틸 -----
_TIME_EXTENDED = re.compile(r"^\(?([0-9]{1,2}):([0-9]{2}):([0-9]{2})\)?$")
_TIME_DETAILED = re.compile(r"^\(?([0-9]{1,2}):([0-9]{2})\)?$")
_TIME_MINUTE  = re.compile(r"^\(?([0-9]{1,2})\s*분\)?$", re.IGNORECASE)
_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")

def _use_local_if_exists(file_name) -> tuple[str | None, str | None]:
    """
    views.py와 같은 폴더의 ./video/<name>.mp4 가 있으면
    그 파일을 '그대로' ffmpeg 입력으로 사용 (오디오는 같은 파일에서 map).
    """
    # base_dir = Path(__file__).resolve().parent
    # local_path = (base_dir / "video" / _LOCAL_OVERRIDE_NAME).resolve()
    
    local_path = VIDEO_DIR / f"{file_name}.mp4"
    if local_path.exists() and local_path.stat().st_size > 0:
        # 로컬 파일 경로를 ffmpeg 입력으로 사용 (URL 아님, 재다운로드/재생성 없음)
        return (local_path.as_posix(), None)  # 단일 소스에서 v+a 추출
    return (None, None)

def _safe_non_negative_float(v: Any) -> Optional[float]:
    try:
        n = float(v)
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None
    
def _timestamp_to_seconds(value: Any) -> Optional[float]:
    if not isinstance(value, str):
        return None
    s = value.strip().strip("()[]{} 　")
    if not s:
        return None
    m = _TIME_EXTENDED.match(s)
    if m:
        h, m_, s_ = m.groups()
        return int(h)*3600 + int(m_)*60 + int(s_)
    m = _TIME_DETAILED.match(s)
    if m:
        m_, s_ = m.groups()
        return int(m_)*60 + int(s_)
    m = _TIME_MINUTE.match(s)
    if m:
        return int(m.group(1)) * 60
    return None



def _safe_positive_float(v: Any) -> Optional[float]:
    n = _safe_non_negative_float(v)
    return n if (n is not None and n > 0) else None


def _normalize_timeline_payload(items: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        start = _safe_non_negative_float(raw.get("start_seconds"))
        ts = raw.get("timestamp")
        if start is None:
            start = _timestamp_to_seconds(ts)
        end = _safe_non_negative_float(raw.get("end_seconds"))
        duration = _safe_positive_float(raw.get("seconds")) or _safe_positive_float(raw.get("duration"))
        if duration is None and start is not None and end is not None and end > start:
            duration = end - start
        if start is None or duration is None:
            continue
        out.append({
            "start": start,
            "duration": duration,
            "id": str(raw.get("id") or idx),
            "timestamp": ts,
            "text": raw.get("text"),
        })
    out.sort(key=lambda x: x["start"])
    return out

def _download_stream(url: str, dest: Path) -> None:
    if not url:
        raise VideoBuildError("missing_video_url")
    _run_ffmpeg(["-y", "-loglevel", "error", "-i", url, "-c", "copy", str(dest)])

def _prepare_source(video_url: str, audio_url: Optional[str], work: Path) -> Path:
    """
    video_url(+audio_url)이 있을 때 하나의 컨테이너로 합쳐 'merged.mkv' 반환.
    가능한 경우 copy, 호환이 안 되면 오디오만 aac로 변환해 copy 혼합.
    """
    v = work / "video_source.mkv"
    _download_stream(video_url, v)
    if audio_url and audio_url != video_url:
        a = work / "audio_source.mka"
        _download_stream(audio_url, a)
        merged = work / "merged_source.mkv"
        # 1차: 전부 copy
        try:
            _run_ffmpeg([
                "-y", "-loglevel", "error",
                "-i", str(v), "-i", str(a),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c", "copy", "-shortest",
                str(merged),
            ])
        except VideoBuildError:
            # 2차: 비디오는 copy, 오디오만 aac로 맞춤
            _run_ffmpeg([
                "-y", "-loglevel", "error",
                "-i", str(v), "-i", str(a),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
                "-shortest",
                str(merged),
            ])
        return merged
    return v

def _resolve_source_input(video_url: str, audio_url: Optional[str], work: Path) -> Path:
    """
    video_url이 로컬 파일 경로이면 그대로 사용.
    아니면 기존처럼 _prepare_source(video_url, audio_url, work)로 병합/다운로드.
    """
    try:
        p = Path(video_url)
        if p.exists() and p.is_file():
            return p.resolve()  # ✅ 로컬 파일 그대로 입력으로 사용
    except Exception:
        pass
    return _prepare_source(video_url, audio_url, work)



def _extract_segments_precise_preroll(
    src: Path,
    segments: Sequence[Dict[str, Any]],
    work: Path,
    *,
    preroll: float = 1.0,   # start 보다 이만큼 앞에서 거칠게 점프(초). 0.5~2.0 권장
) -> List[Path]:
    """
    각 세그먼트를 '정확히' 자르되, 입력 앞 -ss(preseek)로 거칠게 점프한 뒤
    입력 뒤 -ss(post)로 정확히 컷 → 디코드 범위를 최소화해서 빠름.
    비디오/오디오 모두 재인코딩(양끝 정확보장). 인코딩 파라미터를 고정해 concat(copy) 가능.
    """
    segdir = work / "segments_preroll"
    segdir.mkdir(parents=True, exist_ok=True)
    outs: List[Path] = []

    vcodec = os.environ.get("AUTOPART_BUILD_VIDEO_CODEC", "libx264")        # GPU: h264_nvenc / h264_qsv / h264_videotoolbox
    preset = os.environ.get("AUTOPART_BUILD_PRESET", "veryfast")            # 더 빠르게: ultrafast / nvenc p1
    crf    = os.environ.get("AUTOPART_BUILD_CRF", "20")                     # 더 빠르게: 22~26
    acodec = os.environ.get("AUTOPART_BUILD_AUDIO_CODEC", "aac")            # aac가 일반적; 속도↑: "-q:a 2" 사용 가능
    abps   = os.environ.get("AUTOPART_BUILD_AUDIO_BITRATE", "160k")
    hwaccel = os.environ.get("AUTOPART_HWACCEL")                            # "cuda"|"qsv"|"videotoolbox" 등

    for i, seg in enumerate(segments):
        start = max(float(seg["start"]), 0.0)
        dur   = max(float(seg["duration"]), 0.05)

        pre = max(0.0, start - float(preroll))
        post = start - pre  # pre만큼 앞당겼으니 그만큼 다시 정확 시킹

        tgt = segdir / f"segment_{i:03d}.mkv"
        args: List[str] = ["-y", "-loglevel", "error"]
        if hwaccel:
            args += ["-hwaccel", hwaccel]   # 가능 환경에서 디코드 가속
        args += [
            "-threads", "0",                # 멀티스레드
            "-ss", f"{pre:.3f}",            # 1) 거친 빠른 시킹(키프레임 단위)
            "-i", str(src),
            "-ss", f"{post:.3f}",           # 2) 정확 시킹(입력 뒤)
            "-t",  f"{dur:.3f}",
            "-map", "0:v:0", "-map", "0:a:0",
            "-shortest",
            # 비디오 인코딩: 첫 프레임을 즉시 표시 가능한 I-프레임 강제
            "-c:v", vcodec, "-preset", preset, "-crf", crf,
            "-force_key_frames", "expr:gte(t,0)",
            "-pix_fmt", "yuv420p",
            # 오디오 인코딩(정확 끝단 보장; audio copy는 경계 정확도 떨어질 수 있음)
            "-c:a", acodec, "-b:a", abps,
            # 타임스탬프/호환성
            "-fflags", "+genpts",
            "-start_at_zero",
            "-avoid_negative_ts", "make_zero",
            str(tgt),
        ]
        _run_ffmpeg(args)
        outs.append(tgt)

    return outs


def _ffescape(p: Path) -> str:
    # concat demuxer용 안전 이스케이프 (작따 포함 경로)
    return p.as_posix().replace("'", r"'\''")

def _concat_segments_copy(seg_paths: Sequence[Path], work: Path) -> Path:
    if not seg_paths:
        raise VideoBuildError("no_segments")
    abs_paths = []
    for p in seg_paths:
        rp = p.resolve()
        if not rp.exists() or rp.stat().st_size == 0:
            raise VideoBuildError(f"segment_missing_or_empty: {rp}")
        abs_paths.append(rp)

    concat_dir = abs_paths[0].parent
    list_path = concat_dir / "concat.txt"
    list_path.write_text(
        "".join(f"file '{_ffescape(p)}'\n" for p in abs_paths),
        encoding="utf-8",
    )
    out = work / "combined.mkv"
    _run_ffmpeg([
        "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-map", "0",
        "-c", "copy",
        str(out),
    ])
    return out


def _remux_or_transcode_to_mp4(inp: Path, out: Path) -> None:
    # 1) 가능한 경우: 컨테이너만 MP4로 변경 (아주 빠름)
    try:
        _run_ffmpeg([
            "-y", "-loglevel", "error",
            "-i", str(inp),
            "-map", "0",
            "-c", "copy",
            "-movflags", "+faststart",
            str(out),
        ])
        return
    except VideoBuildError:
        pass
    # 2) 폴백: 재인코딩
    _run_ffmpeg([
        "-y", "-loglevel", "error",
        "-i", str(inp),
        "-c:v", os.environ.get("AUTOPART_BUILD_VIDEO_CODEC", "libx264"),
        "-preset", os.environ.get("AUTOPART_BUILD_PRESET", "veryfast"),
        "-crf", os.environ.get("AUTOPART_BUILD_CRF", "20"),
        "-c:a", os.environ.get("AUTOPART_BUILD_AUDIO_CODEC", "aac"),
        "-b:a", os.environ.get("AUTOPART_BUILD_AUDIO_BITRATE", "192k"),
        "-movflags", "+faststart",
        str(out),
    ])
    

def _build_clip_accurate_fast(
    video_url: str,
    audio_url: Optional[str],
    segments: Sequence[Dict[str, Any]],
    title: Optional[str],
) -> Tuple[bytes, str]:
    if not segments:
        raise VideoBuildError("no_segments")
    with tempfile.TemporaryDirectory(prefix="auto_part_accfast_") as td:
        work = Path(td)

        # 로컬 파일이면 그대로, 아니면 기존 병합 사용 (당신이 이미 추가한 함수 그대로 쓰세요)
        src = _resolve_source_input(video_url, audio_url, work)

        # 이중 시킹 + 정확 컷(세그먼트별 짧은 구간만 인코딩)
        preroll = float(os.environ.get("AUTOPART_PREROLL", "1.0"))  # 필요시 0.5~2.0 조정
        segs = _extract_segments_precise_preroll(src, segments, work, preroll=preroll)

        combined = _concat_segments_copy(segs, work)
        out_mp4 = work / "output.mp4"
        _remux_or_transcode_to_mp4(combined, out_mp4)
        data = out_mp4.read_bytes()

    filename_base = _sanitize_filename(title)
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return data, f"{filename_base}_{ts}.mp4"


@csrf_exempt
@require_POST
def build_clip(request):
    
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"error": "Request body must be a JSON object."}, status=400)

    file_name = payload.get("file_name")
    log(request, 4, '영상 조립 시작', '', '', '','','',file_name)
    video_url, audio_url = _use_local_if_exists(file_name)
    if not video_url : return JsonResponse({"error": f"{file_name} not exist"}, status=500)

    
    timeline_payload = payload.get("timeline") or payload.get("segments")
    segments = _normalize_timeline_payload(timeline_payload)
    if not segments:
        return JsonResponse({"error": "timeline must contain at least one valid entry."}, status=400)

    title = payload.get("title") or payload.get("group_title") or payload.get("clip_title")
    
    try:
        file_bytes, filename = _build_clip_accurate_fast(video_url, audio_url, segments, title)
    
    except VideoBuildError as exc:
        msg = str(exc) or "video_build_failed"
        status_code = 400 if msg in {"missing_video_url", "no_segments"} else 500
        return JsonResponse({"error": msg}, status=status_code)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"error": f"unexpected_error: {exc}"}, status=500)

    resp = HttpResponse(file_bytes, content_type="video/mp4")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp["Content-Length"] = str(len(file_bytes))
    # 필요시 CORS 허용 (개발 중 React 로컬에서 부를 때)
    # resp["Access-Control-Allow-Origin"] = "*"
    
    log(request, 4, '영상 조립 완성', '', '', '','','',file_name)
    return resp



























#영상, 음성 업로드



from pathlib import Path
from typing import Any, Dict, List, Optional
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import re

# ----- 환경에 이미 정의되어 있어야 하는 것들 -----
# VIDEO_DIR: Path  (예: Path("/srv/media/video"))
# AUDIO_DIR: Path  (예: Path("/srv/media/audio"))
# ANALYSIS_DIR: Path (예: Path("/srv/media/analysis"))
# _sanitize_filename: 파일명 안전화 함수 (공백/특수문자 제거 등)

_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")

def _sanitize_filename2(name: Optional[str]) -> str:
    name = (name or "").strip()
    if not name:
        return "file"
    return _SAFE_FILENAME_PATTERN.sub("_", name)

def _save_uploaded_file_if_absent(uploaded_file, destination: Path) -> Dict[str, Any]:
    """
    destination 경로에 파일이 이미 있으면 'skipped',
    없으면 저장하고 'saved' 반환
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return {"name": destination.name, "status": "skipped", "reason": "exists"}

    with destination.open("wb") as output:
        for chunk in uploaded_file.chunks():
            output.write(chunk)
    return {"name": destination.name, "status": "saved"}

@csrf_exempt
@require_POST
def upload_media_assets(request: HttpRequest) -> JsonResponse:
    """
    요구사항:
    - mp4, wav, json 파일을 업로드
    - 파일명이 모두 동일(확장자만 다름)이라고 가정
    - 이미 같은 이름의 파일이 해당 디렉토리에 있으면 스킵
    - 저장 위치:
        mp4  -> VIDEO_DIR/<name>.mp4
        wav  -> AUDIO_DIR/<name>.wav
        json -> ANALYSIS_DIR/<name>.json
    - 응답에 파일별 saved/skipped 결과 반환
    """
    if not request.FILES:
        return JsonResponse({"error": "no_files_provided"}, status=400)

    # 공통 base name (전달되면 우선 사용)
    base_input = (
        request.POST.get("filename")
        or request.POST.get("base_name")
        or request.POST.get("file_name")
    )
    base_label = _sanitize_filename2(base_input) if base_input else None

    # 비디오/오디오/분석 파일 수집
    video_files = request.FILES.getlist("video")
    audio_files = request.FILES.getlist("audio")

    # 분석(json) 파일은 필드명이 다양할 수 있으니 폭넓게 수집
    analysis_files = []
    for key in ("analysis", "analysis_files", "faster_whisper", "transcript"):
        analysis_files.extend(request.FILES.getlist(key))
    # 혹시 다른 키로 왔을 수도 있으니 나머지도 스캔
    for key in request.FILES:
        if key in {"video", "audio", "analysis", "analysis_files", "faster_whisper", "transcript"}:
            continue
        analysis_files.extend(request.FILES.getlist(key))

    # 필수: mp4 / wav / json 최소 1개씩은 있어야 함
    if not video_files:
        return JsonResponse({"error": "missing_video_mp4"}, status=400)
    if not audio_files:
        return JsonResponse({"error": "missing_audio_wav"}, status=400)
    if not analysis_files:
        return JsonResponse({"error": "missing_analysis_json"}, status=400)

    # 하나만 받도록 강제(요구사항에 맞춤). 여러 개가 오면 첫 번째만 사용.
    video_file = video_files[0]
    audio_file = audio_files[0]
    analysis_file = analysis_files[0]

    # 원본 파일명에서 스템 추출 (base_label이 있으면 우선)
    video_name = (video_file.name or "video.mp4").lower()
    audio_name = (audio_file.name or "audio.wav").lower()
    analysis_name = (analysis_file.name or "analysis.json").lower()

    # 확장자 검증
    if not video_name.endswith(".mp4"):
        return JsonResponse({"error": "video_must_be_mp4"}, status=400)
    if not audio_name.endswith(".wav"):
        return JsonResponse({"error": "audio_must_be_wav"}, status=400)
    if not analysis_name.endswith(".json"):
        return JsonResponse({"error": "analysis_must_be_json"}, status=400)

    # 스템(확장자 없는 이름)
    if base_label:
        stem = base_label
    else:
        # 서로 다른 이름으로 올려도 "동일한 이름"으로 맞춰 저장하려면
        # 여기서 하나를 기준으로 삼는다(비디오 이름을 기준으로).
        stem = _sanitize_filename(Path(video_name).stem)

    # 목적지 경로
    video_dest = VIDEO_DIR / f"{stem}.mp4"
    audio_dest = AUDIO_DIR / f"{stem}.wav"
    analysis_dest = ANALYSIS_DIR / f"{stem}.json"

    # 저장(이미 있으면 skip)
    video_result = _save_uploaded_file_if_absent(video_file, video_dest)
    audio_result = _save_uploaded_file_if_absent(audio_file, audio_dest)
    analysis_result = _save_uploaded_file_if_absent(analysis_file, analysis_dest)

    response_payload = {
        "video": video_result,       # {"name": "...", "status": "saved|skipped", ...}
        "audio": audio_result,
        "analysis": analysis_result,
        "base_name": stem,
    }
    return JsonResponse(response_payload, status=201)














# === Greedy word-chain matcher (hint±1s, no canon map) ===
import json, unicodedata, re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

try:
    from rapidfuzz import fuzz
    HAVE_RF = True
except Exception:
    from difflib import SequenceMatcher
    HAVE_RF = False

_PUNCT_RE = re.compile(r"[^\w가-힣]+")

def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    return " ".join(s.split())

def _normalize_spaceless(s: str) -> str:
    return _normalize(s).replace(" ", "")

def _sim_word(a: str, b: str) -> float:
    a1, b1 = _normalize(a), _normalize(b)
    a2, b2 = a1.replace(" ", ""), b1.replace(" ", "")
    if HAVE_RF:
        return max(
            fuzz.ratio(a1, b1)/100.0,
            fuzz.partial_ratio(a1, b1)/100.0,
            fuzz.ratio(a2, b2)/100.0,
            fuzz.partial_ratio(a2, b2)/100.0,
        )
    else:
        return max(
            SequenceMatcher(None, a1, b1).ratio(),
            SequenceMatcher(None, a2, b2).ratio()
        )
    
def _sim_phrase(a: str, b: str) -> float:
    """두 단어를 이어 붙인 '구' 비교(스페이스/부분 포함까지 고려)"""
    a1, b1 = _normalize(a), _normalize(b)
    a2, b2 = a1.replace(" ", ""), b1.replace(" ", "")
    if HAVE_RF:
        return max(
            fuzz.WRatio(a1, b1)/100.0,
            fuzz.partial_ratio(a1, b1)/100.0,
            fuzz.ratio(a2, b2)/100.0,
            fuzz.partial_ratio(a2, b2)/100.0,
        )
    else:
        return max(
            SequenceMatcher(None, a1, b1).ratio(),
            SequenceMatcher(None, a2, b2).ratio()
        )

def _load_segments_from_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["segments"] if isinstance(data, dict) and "segments" in data else data

def _flatten_words(segments: List[Dict]) -> List[Dict]:
    out = []
    for seg in segments:
        for w in seg.get("words") or []:
            txt = (w.get("word") or "").strip()
            try:
                s = float(w.get("start")); e = float(w.get("end"))
            except (TypeError, ValueError):
                continue
            if txt and e > s:
                out.append({"w": txt, "start": s, "end": e})
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out





def _best_anchor_index(words: List[Dict], target_ns: str, start_hint: float, window: float = 1.0) -> Optional[int]:
    """start_hint±window 안에서 타깃 시작부와 가장 비슷한 '단일 word'를 앵커로 선택"""
    lo, hi = start_hint - window, start_hint + window
    cands = []
    for i, w in enumerate(words):
        t = w["start"]
        if lo <= t <= hi:
            w_ns = _normalize_spaceless(words[i]["w"])
            if not w_ns:
                continue
            print(f"w_ns : {w_ns}")
            # 타깃의 시작부(같은 길이)와 비교
            prefix = target_ns[:len(w_ns)]
            sim = _sim_word(w_ns, prefix)
            print(f"sim : {sim}")
            cands.append((sim, -abs(t - start_hint), i))  # 유사도, 시간근접도 tie-break
    if not cands:
        return None
    cands.sort(reverse=True)
    return cands[0][2]


def _span_match_by_len(
    words: List[Dict],
    target_text: str,
    start_hint: float,
    *,
    window_sec: float = 1.0,
    refine_margin: int = 2,              # 좌우로 몇 개까지 확장/축소 탐색
    len_penalty_weight: float = 0.2,     # 길이 오차 패널티 가중치(0~1)
) -> Optional[Dict]:
    """
    절차:
      1) start_hint±window 안에서 타깃 시작부와 가장 비슷한 단어를 앵커로 잡음.
      2) 타깃(공백 제거) 길이 L까지, 앵커부터 앞으로 word들을 공백 제거로 이어붙임.
      3) 얻어진 [i0..j0] 구간을 기준으로 좌우 ±refine_margin 안에서 최적 유사도 구간 재탐색.
      4) 최종 start/end와 유사도 반환.
    """
    if not words:
        return None

    target_ns = _normalize_spaceless(target_text)
    if not target_ns:
        return None
    L = len(target_ns)

    # 1) 앵커 선정
    anchor = _best_anchor_index(words, target_ns, start_hint, window=window_sec)
    if anchor is None:
        return None

    # 2) 길이 맞출 때까지 앞으로 확장
    i0 = anchor
    j0 = anchor
    acc = _normalize_spaceless(words[anchor]["w"])
    # 너무 짧으면 앞으로 확장
    while len(acc) < L and j0 + 1 < len(words):
        j0 += 1
        acc += _normalize_spaceless(words[j0]["w"])

    # 3) 좌우 ±refine_margin 탐색으로 최고 점수 구간 선택
    best = None
    n = len(words)

    def seg_score(i: int, j: int) -> Tuple[float, float, int, int, int]:
        seg_ns = "".join(_normalize_spaceless(words[k]["w"]) for k in range(i, j + 1))
        seg_len = len(seg_ns)

        if HAVE_RF:
            base = fuzz.ratio(seg_ns, target_ns) / 100.0
            partial = fuzz.partial_ratio(seg_ns, target_ns) / 100.0
            s = max(base, 0.6 * partial)
        else:
            s = SequenceMatcher(None, seg_ns, target_ns).ratio()

        pref_len = min(len(seg_ns), max(6, len(target_ns)//10))
        pref_a = seg_ns[:pref_len]; pref_b = target_ns[:pref_len]
        pref = fuzz.ratio(pref_a, pref_b) / 100.0 if HAVE_RF else SequenceMatcher(None, pref_a, pref_b).ratio()
        s += 0.1 * pref

        # 비대칭 길이 패널티
        over = max(0, seg_len - L)
        lack = max(0, L - seg_len)
        alpha, beta = 2.0, 1.0  # 초과에 더 큰 벌점
        len_pen = max(0.0, 1.0 - (alpha*over + beta*lack) / max(L, 1))

        score = (1.0 - len_penalty_weight) * s + (len_penalty_weight) * len_pen
        return (score, s, i, j, seg_len)

    i_min = max(0, i0 - refine_margin)
    i_max = i0
    j_min = max(j0 - refine_margin, i0)
    j_max = j0

    best = None
    best_score = None
    best_s = None
    best_i = best_j = None
    best_len = None

    for i in range(i_min, i_max + 1):
        for j in range(j_min, j_max + 1):
            if j < i:
                continue
            score, s, ii, jj, seg_len = seg_score(i, j)  # ← seg_score가 (score, s, i, j, seg_len) 반환하도록 해둔 버전

            if best is None or score > best_score:
                best = (score, s, ii, jj, seg_len)
                best_score, best_s, best_i, best_j, best_len = best
                continue

            if score == best_score:
                # tie-break: 타깃 길이 근접성 ▶ j가 j0 넘지 않음 ▶ 더 짧은 구간 ▶ 더 이른 시작
                tie_cand = (-abs(seg_len - L), 0 if jj <= j0 else 1, -(jj - ii), words[ii]["start"])
                tie_best = (-abs(best_len - L), 0 if best_j <= j0 else 1, -(best_j - best_i), words[best_i]["start"])
                if tie_cand > tie_best:
                    best = (score, s, ii, jj, seg_len)
                    best_score, best_s, best_i, best_j, best_len = best

    if best is None:
        return None

    final_score, base_sim, i_best, j_best, seg_len = best
    start_t = float(words[i_best]["start"])
    end_t   = float(words[j_best]["end"])
    matched = [w["w"] for w in words[i_best:j_best + 1]]

    return {
        "ok": True,
        "timeline": {"start": start_t, "end": end_t, "similarity": round(final_score, 4)},
        "start": start_t,
        "end": end_t,
        "similarity_avg": round(final_score, 4),
        "similarity_base": round(base_sim, 4),   # 공백제거 기준 순수 유사도
        "method": "len_match(anchor±1s → fill_to_length → refine±2)",
        "matched_words": matched,
        "target_len_ns": len(target_ns),
        "start_word_index": i_best,
        "end_word_index": j_best,
    }









# === 엔드포인트 ===
@csrf_exempt
@require_POST
def stt_find_span_cached_greedy(request: HttpRequest) -> JsonResponse:
    """
    요청 JSON:
      {
        "file_name": "250608",          # .wav 스템 (캐시: 250608.stt.json)
        "start_hint": 1484.24,          # 초 (항상 1초 이상)
        "target_text": "...",
        "window_sec": 1.0,              # 선택
        "per_word_min_sim": 0.60,       # 선택
        "lookahead_words": 8,           # 선택
        "lookahead_time": 3.0           # 선택(초)
      }
    응답:
      {
        "ok": true,
        "start": 1484.24,
        "end": 1506.76,
        "similarity_avg": 0.91,
        "similarity_min": 0.72,
        "matched_words": [...],
        ...
      }
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    file_name = (payload.get("file_name") or "").strip()
    start_hint = float(payload.get("start_hint", 0.0))
    target_text = (payload.get("target_text") or "").strip()
    window_sec = float(payload.get("window_sec", 2.0))
    per_word_min_sim = float(payload.get("per_word_min_sim", 0.60))
    lookahead_words = int(payload.get("lookahead_words", 12))
    lookahead_time = float(payload.get("lookahead_time", 4.0))
    if not file_name or not target_text or start_hint <= 0:
        return JsonResponse({"ok": False, "error": "missing_params"}, status=400)

    json_path = (ANALYSIS_DIR / f"{file_name}.json")
    if not json_path.exists():
        return JsonResponse({"ok": False, "error": f"cache_not_found: {json_path.name}"}, status=404)

    try:
        segments = _load_segments_from_json(json_path)
        words = _flatten_words(segments)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"load_failed: {e}"}, status=500)

    hit = _span_match_by_len(
        words, target_text, start_hint,
        window_sec=window_sec,
        refine_margin=2,
        len_penalty_weight=0.2
    )

    if not hit:
        return JsonResponse({
            "ok": False,
            "error": "no_chain_match",
        }, status=404)
    
    start = str(hit["timeline"]["start"] or "")
    end = str(hit["timeline"]["end"] or "")
    similarity = str(hit["timeline"]["similarity"] or "")
    log(request, 3, 'STT-LOCAL', '', '', '',start,end,similarity)
    return JsonResponse({"ok": True, **hit})
