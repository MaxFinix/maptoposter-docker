"""
FastAPI application for generating and downloading map posters.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import sys
import time
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from poster_service import (
    CACHE_DIR,
    PosterOptions,
    ensure_runtime_paths_writable,
    get_poster_media_type,
    get_safe_poster_path,
    get_theme_catalog,
    list_generated_files,
    parse_coordinate,
)
from web_i18n import (
    CM_PER_INCH,
    COOKIE_MAX_AGE,
    DEFAULT_LANGUAGE,
    LANGUAGE_COOKIE_NAME,
    build_generation_failure_message,
    build_js_text,
    format_created_message,
    format_metric_input,
    format_modified_label,
    get_text_bundle,
    localize_theme_catalog,
    normalize_language,
    translate_error_message,
)


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        checked_paths = ensure_runtime_paths_writable()
    except Exception as exc:
        print(f"Startup check failed: {exc}", flush=True)
        raise

    for label, path in checked_paths.items():
        print(f"Startup check OK: {label} -> {path}", flush=True)
    yield


app = FastAPI(title="MaptoPoster", version="0.3.0", lifespan=lifespan)
JOB_POLL_INTERVAL_MS = 2000
JOB_LOCK = threading.Lock()
JOBS_ROOT = CACHE_DIR / "jobs"
ACTIVE_JOB_ID: str | None = None


@dataclass(slots=True)
class GenerationJob:
    id: str
    status: str
    created_at: float
    started_at: float
    finished_at: float | None
    language: str
    message: str | None
    error: str | None
    generated_names: list[str]
    step: str | None
    status_file: Path
    output_dir: Path
    options_file: Path
    process: subprocess.Popen[bytes] | None


JOBS: dict[str, GenerationJob] = {}


def build_form_data(language: str, overrides: dict[str, str] | None = None) -> dict[str, str]:
    form = {
        "city": "",
        "country": "",
        "theme": "terracotta",
        "distance": "18000",
        "width": format_metric_input(12 * CM_PER_INCH, language),
        "height": format_metric_input(16 * CM_PER_INCH, language),
        "format": "png",
        "latitude": "",
        "longitude": "",
        "country_label": "",
        "display_city": "",
        "display_country": "",
        "font_family": "",
        "all_themes": "",
        "lang": language,
    }
    if overrides:
        form.update(overrides)
    return form


def parse_int(value: str, field_name: str, language: str) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        message = get_text_bundle(language)["errors"]["whole_number"].format(field=field_name)
        raise ValueError(message) from exc
    return parsed


def parse_float(value: str, field_name: str, language: str) -> float:
    try:
        parsed = float(value.strip().replace(",", "."))
    except ValueError as exc:
        message = get_text_bundle(language)["errors"]["number"].format(field=field_name)
        raise ValueError(message) from exc
    return parsed


def centimeters_to_inches(value_cm: float) -> float:
    return value_cm / CM_PER_INCH


def resolve_language(request: Request, submitted_language: str | None = None) -> str:
    if submitted_language:
        return normalize_language(submitted_language)

    query_language = request.query_params.get("lang")
    if query_language:
        return normalize_language(query_language)

    return normalize_language(request.cookies.get(LANGUAGE_COOKIE_NAME, DEFAULT_LANGUAGE))


def with_language_cookie(response: Response, language: str) -> Response:
    response.set_cookie(
        key=LANGUAGE_COOKIE_NAME,
        value=language,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
    )
    return response


def relative_url_for(route_name: str, **path_params: str) -> str:
    return str(app.url_path_for(route_name, **path_params))


def snapshot_job(job: GenerationJob | None) -> GenerationJob | None:
    if job is None:
        return None
    return replace(job, generated_names=list(job.generated_names))


def job_dir(job_id: str) -> Path:
    return JOBS_ROOT / job_id


def write_json_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def read_json_file(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Failed to read job status file '{path}': {exc}", flush=True)
        return None


def job_status_payload(job: GenerationJob) -> dict[str, object]:
    return {
        "job_id": job.id,
        "language": job.language,
        "status": job.status,
        "message": job.message,
        "error": job.error,
        "step": job.step,
        "generated_names": list(job.generated_names),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "updated_at": time.time(),
        "can_cancel": job.status == "running",
    }


def persist_job_status(job: GenerationJob) -> None:
    write_json_file(job.status_file, job_status_payload(job))


def cleanup_job_artifacts(job: GenerationJob) -> None:
    shutil.rmtree(job.output_dir, ignore_errors=True)
    try:
        job.options_file.unlink(missing_ok=True)
    except OSError as exc:
        print(f"Failed to delete job options file '{job.options_file}': {exc}", flush=True)


def apply_status_payload(job: GenerationJob, payload: dict[str, object]) -> None:
    disk_status = payload.get("status")
    if (
        job.status == "canceling"
        and disk_status == "running"
    ):
        job.step = payload.get("step") if isinstance(payload.get("step"), str) else job.step
        return

    if isinstance(disk_status, str):
        job.status = disk_status
    if "message" in payload:
        job.message = payload.get("message") if isinstance(payload.get("message"), str) else None
    if "error" in payload:
        job.error = payload.get("error") if isinstance(payload.get("error"), str) else None
    if "step" in payload:
        job.step = payload.get("step") if isinstance(payload.get("step"), str) else None
    if isinstance(payload.get("generated_names"), list):
        job.generated_names = [str(item) for item in payload["generated_names"]]
    if isinstance(payload.get("created_at"), (int, float)):
        job.created_at = float(payload["created_at"])
    if isinstance(payload.get("started_at"), (int, float)):
        job.started_at = float(payload["started_at"])
    if isinstance(payload.get("finished_at"), (int, float)):
        job.finished_at = float(payload["finished_at"])
    elif payload.get("finished_at") is None:
        job.finished_at = None


def finalize_job_canceled(job: GenerationJob) -> None:
    text = get_text_bundle(job.language)["messages"]
    job.status = "canceled"
    job.message = text["job_canceled"]
    job.error = None
    job.finished_at = time.time()
    job.step = None
    job.generated_names = []
    cleanup_job_artifacts(job)
    persist_job_status(job)


def finalize_job_failed(job: GenerationJob, error_message: str) -> None:
    job.status = "failed"
    job.message = None
    job.error = error_message
    job.finished_at = time.time()
    cleanup_job_artifacts(job)
    persist_job_status(job)


def terminate_job_process(job: GenerationJob) -> None:
    process = job.process
    if process is None or process.poll() is not None:
        return

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            process.terminate()
        except OSError:
            return

    try:
        process.wait(timeout=1.5)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except OSError:
            return

    try:
        process.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        print(f"Timed out while waiting for worker process of job {job.id} to stop", flush=True)


def sync_job_state(job_id: str | None) -> GenerationJob | None:
    global ACTIVE_JOB_ID

    if not job_id:
        return None

    with JOB_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return None

    payload = read_json_file(job.status_file)
    with JOB_LOCK:
        current = JOBS.get(job_id)
        if current is None:
            return None
        if payload is not None:
            apply_status_payload(current, payload)
        job = current

    process = job.process
    if process is not None and process.poll() is not None:
        with JOB_LOCK:
            current = JOBS.get(job_id)
            if current is None:
                return None
            current.process = None
            job = current

        if job.status in {"running", "canceling"}:
            if job.status == "canceling":
                print(f"Generate job canceled {job.id}", flush=True)
                finalize_job_canceled(job)
            else:
                worker_exited = get_text_bundle(job.language)["messages"]["worker_exited"]
                error_message = build_generation_failure_message(worker_exited, job.language)
                print(f"Generate job failed {job.id}: worker exited unexpectedly", flush=True)
                finalize_job_failed(job, error_message)

    if job.status not in {"running", "canceling"}:
        with JOB_LOCK:
            if ACTIVE_JOB_ID == job.id:
                ACTIVE_JOB_ID = None

    with JOB_LOCK:
        return snapshot_job(JOBS.get(job_id))


def get_job(job_id: str | None) -> GenerationJob | None:
    return sync_job_state(job_id)


def get_active_job() -> GenerationJob | None:
    with JOB_LOCK:
        active_job_id = ACTIVE_JOB_ID
    return sync_job_state(active_job_id)


def build_job_payload(
    request: Request,
    job: GenerationJob,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "job_id": job.id,
        "status": job.status,
        "lang": job.language,
        "message": job.message,
        "error": job.error,
        "step": job.step,
        "can_cancel": job.status == "running",
        "elapsed_seconds": max(
            0,
            int(((job.finished_at or time.time()) - job.started_at)),
        ),
    }

    if job.status in {"running", "canceling", "failed", "canceled"}:
        return payload

    payload.update(build_generate_payload(request, set(job.generated_names), job.language))
    return payload


def start_generation_job(
    options: PosterOptions,
    language: str,
    client_host: str,
) -> tuple[GenerationJob, bool]:
    global ACTIVE_JOB_ID
    active_job = get_active_job()
    if active_job is not None and active_job.status in {"running", "canceling"}:
        return active_job, False

    job_id = uuid4().hex
    created_at = time.time()
    current_job_dir = job_dir(job_id)
    status_file = current_job_dir / "status.json"
    output_dir = current_job_dir / "output"
    options_file = current_job_dir / "options.json"
    current_job_dir.mkdir(parents=True, exist_ok=True)
    write_json_file(options_file, asdict(options))

    job = GenerationJob(
        id=job_id,
        status="running",
        created_at=created_at,
        started_at=created_at,
        finished_at=None,
        language=language,
        message=get_text_bundle(language)["messages"]["job_started"],
        error=None,
        generated_names=[],
        step=None,
        status_file=status_file,
        output_dir=output_dir,
        options_file=options_file,
        process=None,
    )
    persist_job_status(job)

    command = [
        sys.executable,
        str(BASE_DIR / "web_job_runner.py"),
        "--job-id",
        job.id,
        "--language",
        language,
        "--status-file",
        str(status_file),
        "--output-dir",
        str(output_dir),
        "--options-file",
        str(options_file),
    ]
    process = subprocess.Popen(
        command,
        cwd=str(BASE_DIR),
        env=os.environ.copy(),
        start_new_session=True,
    )
    job.process = process

    with JOB_LOCK:
        JOBS[job.id] = job
        ACTIVE_JOB_ID = job.id

    print(
        f"Generate job started {job.id} from {client_host}: "
        f"city={options.city!r}, country={options.country!r}, theme={options.theme!r}, "
        f"all_themes={options.all_themes}, format={options.output_format!r}, lang={language!r}",
        flush=True,
    )
    return snapshot_job(job), True


def build_language_urls(request: Request) -> dict[str, str]:
    query_params = dict(request.query_params)
    query_params.pop("message", None)
    query_params.pop("error", None)

    base_path = relative_url_for("index")
    urls: dict[str, str] = {}
    for language in ("de", "en"):
        query = query_params | {"lang": language}
        urls[language] = base_path if not query else f"{base_path}?{urlencode(query)}"
    return urls


def render_index(
    request: Request,
    *,
    language: str,
    message: str | None = None,
    error: str | None = None,
    form_data: dict[str, str] | None = None,
    focus_name: str | None = None,
    initial_job_id: str | None = None,
) -> HTMLResponse:
    posters = enrich_posters(request, list_generated_files(), language)
    context = {
        "request": request,
        "lang": language,
        "message": message,
        "error": error,
        "text": get_text_bundle(language),
        "js_text": build_js_text(language),
        "form": build_form_data(language, form_data),
        "themes": localize_theme_catalog(get_theme_catalog(), language),
        "language_urls": build_language_urls(request),
        "posters": posters,
        "featured_poster": choose_featured_poster(posters, focus_name),
        "initial_job_id": initial_job_id,
        "job_poll_interval_ms": JOB_POLL_INTERVAL_MS,
    }
    return templates.TemplateResponse("index.html", context)


def redirect_to_index(
    request: Request,
    *,
    language: str,
    message: str | None = None,
    error: str | None = None,
    focus_name: str | None = None,
    job_id: str | None = None,
) -> RedirectResponse:
    query: dict[str, str] = {"lang": language}
    if message:
        query["message"] = message
    if error:
        query["error"] = error
    if focus_name:
        query["focus"] = focus_name
    if job_id:
        query["job"] = job_id

    url = relative_url_for("index")
    if query:
        url = f"{url}?{urlencode(query)}"

    return RedirectResponse(url=url, status_code=303)


def wants_json_response(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/json" in accept.lower()


def enrich_posters(
    request: Request,
    posters: list[dict[str, object]],
    language: str,
) -> list[dict[str, object]]:
    enriched = []
    for poster in posters:
        name = str(poster["name"])
        media_type = str(poster["media_type"])
        enriched.append(
            {
                key: value
                for key, value in poster.items()
                if key != "path"
            }
            | {
                "modified_label": format_modified_label(
                    float(poster["modified_timestamp"]), language
                ),
                "preview_url": relative_url_for("preview_poster", filename=name),
                "open_url": relative_url_for("preview_poster", filename=name),
                "download_url": relative_url_for("download_poster", filename=name),
                "is_previewable": media_type.startswith("image/") or media_type == "application/pdf",
                "is_image_previewable": media_type.startswith("image/"),
                "is_pdf": media_type == "application/pdf",
            }
        )
    return enriched


def choose_featured_poster(
    posters: list[dict[str, object]], focus_name: str | None = None
) -> dict[str, object] | None:
    if focus_name:
        for poster in posters:
            if poster["name"] == focus_name:
                return poster
    return posters[0] if posters else None


def build_generate_payload(
    request: Request,
    generated_names: set[str],
    language: str,
) -> dict[str, object]:
    posters = enrich_posters(request, list_generated_files(), language)
    generated = [poster for poster in posters if poster["name"] in generated_names]
    return {"posters": posters, "generated": generated, "lang": language}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    language = resolve_language(request)
    message = request.query_params.get("message")
    error = request.query_params.get("error")
    focus_name = request.query_params.get("focus")
    initial_job_id: str | None = None

    requested_job = get_job(request.query_params.get("job"))
    if requested_job is None:
        if request.query_params.get("job") and error is None:
            error = get_text_bundle(language)["messages"]["job_not_found"]
    elif requested_job.status in {"running", "canceling"}:
        initial_job_id = requested_job.id
        if message is None:
            message = requested_job.message
    elif requested_job.status == "succeeded":
        if message is None:
            message = requested_job.message
        if focus_name is None and requested_job.generated_names:
            focus_name = requested_job.generated_names[0]
    elif requested_job.status == "canceled":
        if message is None:
            message = requested_job.message
    elif requested_job.status == "failed" and error is None:
        error = requested_job.error

    response = render_index(
        request,
        language=language,
        message=message,
        error=error,
        focus_name=focus_name,
        initial_job_id=initial_job_id,
    )
    return with_language_cookie(response, language)


@app.get("/generate")
async def generate_redirect(request: Request) -> RedirectResponse:
    language = resolve_language(request)
    response = redirect_to_index(request, language=language)
    return with_language_cookie(response, language)


@app.post("/generate", response_model=None)
async def generate(
    request: Request,
    city: str = Form(""),
    country: str = Form(""),
    theme: str = Form("terracotta"),
    distance: str = Form("18000"),
    width: str = Form("30.5"),
    height: str = Form("40.6"),
    output_format: str = Form("png", alias="format"),
    latitude: str = Form(""),
    longitude: str = Form(""),
    country_label: str = Form(""),
    display_city: str = Form(""),
    display_country: str = Form(""),
    font_family: str = Form(""),
    all_themes: str = Form(""),
    lang: str = Form(DEFAULT_LANGUAGE),
) -> RedirectResponse | JSONResponse:
    language = resolve_language(request, lang)
    text = get_text_bundle(language)
    client_host = request.client.host if request.client else "unknown"

    try:
        options = PosterOptions(
            city=city,
            country=country,
            theme=theme,
            all_themes=all_themes == "on",
            distance=parse_int(distance, text["labels"]["distance"], language),
            width=centimeters_to_inches(parse_float(width, text["labels"]["width"], language)),
            height=centimeters_to_inches(parse_float(height, text["labels"]["height"], language)),
            country_label=country_label,
            display_city=display_city,
            display_country=display_country,
            font_family=font_family,
            latitude=parse_coordinate(latitude),
            longitude=parse_coordinate(longitude),
            output_format=output_format,
        )
    except ValueError as exc:
        localized_error = translate_error_message(str(exc), language)
        print(f"Generate request validation failed from {client_host}: {localized_error}", flush=True)
        if wants_json_response(request):
            response = JSONResponse(
                {"ok": False, "error": localized_error, "lang": language},
                status_code=400,
            )
            return with_language_cookie(response, language)
        response = redirect_to_index(request, language=language, error=localized_error)
        return with_language_cookie(response, language)

    active_job, created = start_generation_job(options, language, client_host)
    if not created:
        error_message = text["messages"]["job_running"]
        print(
            f"Generate request rejected from {client_host}: job {active_job.id} already running",
            flush=True,
        )
        if wants_json_response(request):
            response = JSONResponse(
                {
                    "ok": False,
                    "job_id": active_job.id,
                    "status": active_job.status,
                    "step": active_job.step,
                    "can_cancel": active_job.status == "running",
                    "error": error_message,
                    "lang": language,
                },
                status_code=409,
            )
            return with_language_cookie(response, language)

        response = redirect_to_index(
            request,
            language=language,
            error=error_message,
            job_id=active_job.id,
        )
        return with_language_cookie(response, language)

    if wants_json_response(request):
        response = JSONResponse(
            {
                "ok": True,
                "job_id": active_job.id,
                "status": active_job.status,
                "message": active_job.message,
                "step": active_job.step,
                "can_cancel": active_job.status == "running",
                "lang": language,
            },
            status_code=202,
        )
        return with_language_cookie(response, language)

    response = redirect_to_index(
        request,
        language=language,
        message=active_job.message,
        job_id=active_job.id,
    )
    return with_language_cookie(response, language)


@app.get("/jobs/{job_id}", response_model=None)
async def job_status(request: Request, job_id: str) -> JSONResponse:
    job = get_job(job_id)
    if job is None:
        language = resolve_language(request)
        response = JSONResponse(
            {
                "ok": False,
                "job_id": job_id,
                "status": "missing",
                "error": get_text_bundle(language)["messages"]["job_not_found"],
                "lang": language,
            },
            status_code=404,
        )
        return with_language_cookie(response, language)

    response = JSONResponse(build_job_payload(request, job))
    return with_language_cookie(response, job.language)


@app.post("/jobs/{job_id}/cancel", response_model=None)
async def cancel_job(request: Request, job_id: str) -> JSONResponse:
    job = get_job(job_id)
    if job is None:
        language = resolve_language(request)
        response = JSONResponse(
            {
                "ok": False,
                "job_id": job_id,
                "status": "missing",
                "error": get_text_bundle(language)["messages"]["job_not_found"],
                "lang": language,
            },
            status_code=404,
        )
        return with_language_cookie(response, language)

    if job.status == "canceling":
        response = JSONResponse(build_job_payload(request, job), status_code=202)
        return with_language_cookie(response, job.language)

    if job.status != "running":
        error_message = get_text_bundle(job.language)["messages"]["job_cancel_unavailable"]
        response = JSONResponse(
            {
                **build_job_payload(request, job),
                "ok": False,
                "error": error_message,
            },
            status_code=409,
        )
        return with_language_cookie(response, job.language)

    with JOB_LOCK:
        current = JOBS.get(job_id)
        if current is None:
            language = resolve_language(request)
            response = JSONResponse(
                {
                    "ok": False,
                    "job_id": job_id,
                    "status": "missing",
                    "error": get_text_bundle(language)["messages"]["job_not_found"],
                    "lang": language,
                },
                status_code=404,
            )
            return with_language_cookie(response, language)

        current.status = "canceling"
        current.message = get_text_bundle(current.language)["messages"]["job_canceling"]
        current.error = None
        persist_job_status(current)
        job = snapshot_job(current)

    print(f"Generate job cancel requested {job_id}", flush=True)
    terminate_job_process(job)
    updated_job = get_job(job_id) or job
    response = JSONResponse(build_job_payload(request, updated_job), status_code=202)
    return with_language_cookie(response, updated_job.language)


@app.get("/files/{filename}")
async def preview_poster(filename: str) -> FileResponse:
    path = get_safe_poster_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(
        path=path,
        filename=path.name,
        media_type=get_poster_media_type(path),
        content_disposition_type="inline",
    )


@app.get("/download/{filename}")
async def download_poster(filename: str) -> FileResponse:
    path = get_safe_poster_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(
        path=path,
        filename=path.name,
        media_type=get_poster_media_type(path),
        content_disposition_type="attachment",
    )


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = int(os.environ.get("APP_PORT", "6641"))
    uvicorn.run("web_app:app", host=host, port=port)
