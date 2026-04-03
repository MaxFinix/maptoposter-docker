"""
FastAPI application for generating and downloading map posters.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from poster_service import (
    PosterOptions,
    ensure_runtime_paths_writable,
    generate_posters,
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


def get_job(job_id: str | None) -> GenerationJob | None:
    if not job_id:
        return None

    with JOB_LOCK:
        return snapshot_job(JOBS.get(job_id))


def get_active_job() -> GenerationJob | None:
    with JOB_LOCK:
        if ACTIVE_JOB_ID is None:
            return None
        return snapshot_job(JOBS.get(ACTIVE_JOB_ID))


def build_job_payload(
    request: Request,
    job: GenerationJob,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": job.status != "failed",
        "job_id": job.id,
        "status": job.status,
        "lang": job.language,
        "message": job.message,
    }

    if job.status == "running":
        payload["elapsed_seconds"] = max(0, int(time.time() - job.started_at))
        return payload

    if job.status == "failed":
        payload["error"] = job.error
        return payload

    payload["elapsed_seconds"] = (
        max(0, int((job.finished_at or time.time()) - job.started_at))
    )
    payload.update(build_generate_payload(request, set(job.generated_names), job.language))
    return payload


def run_generation_job(job_id: str, options: PosterOptions, client_host: str) -> None:
    global ACTIVE_JOB_ID

    job = get_job(job_id)
    if job is None:
        return

    try:
        generated = generate_posters(options)
    except ValueError as exc:
        localized_error = translate_error_message(str(exc), job.language)
        with JOB_LOCK:
            current = JOBS.get(job_id)
            if current is not None:
                current.status = "failed"
                current.finished_at = time.time()
                current.error = localized_error
                current.message = None
                if ACTIVE_JOB_ID == job_id:
                    ACTIVE_JOB_ID = None
        print(f"Generate job failed {job_id} from {client_host}: {localized_error}", flush=True)
        return
    except Exception as exc:
        localized_error = build_generation_failure_message(
            translate_error_message(str(exc), job.language),
            job.language,
        )
        with JOB_LOCK:
            current = JOBS.get(job_id)
            if current is not None:
                current.status = "failed"
                current.finished_at = time.time()
                current.error = localized_error
                current.message = None
                if ACTIVE_JOB_ID == job_id:
                    ACTIVE_JOB_ID = None
        print(f"Generate job failed {job_id} from {client_host}: {exc}", flush=True)
        return

    generated_names = [path.name for path in generated]
    names = ", ".join(generated_names)
    message = format_created_message(len(generated_names), names, job.language)

    with JOB_LOCK:
        current = JOBS.get(job_id)
        if current is not None:
            current.status = "succeeded"
            current.finished_at = time.time()
            current.generated_names = generated_names
            current.message = message
            current.error = None
            if ACTIVE_JOB_ID == job_id:
                ACTIVE_JOB_ID = None

    print(
        f"Generate job completed {job_id} from {client_host}: "
        f"{len(generated_names)} file(s) -> {names}",
        flush=True,
    )


def start_generation_job(
    options: PosterOptions,
    language: str,
    client_host: str,
) -> tuple[GenerationJob, bool]:
    global ACTIVE_JOB_ID

    with JOB_LOCK:
        active_job = JOBS.get(ACTIVE_JOB_ID) if ACTIVE_JOB_ID is not None else None
        if active_job is not None and active_job.status == "running":
            return snapshot_job(active_job), False

        job = GenerationJob(
            id=uuid4().hex,
            status="running",
            created_at=time.time(),
            started_at=time.time(),
            finished_at=None,
            language=language,
            message=get_text_bundle(language)["messages"]["job_started"],
            error=None,
            generated_names=[],
        )
        JOBS[job.id] = job
        ACTIVE_JOB_ID = job.id

    print(
        f"Generate job started {job.id} from {client_host}: "
        f"city={options.city!r}, country={options.country!r}, theme={options.theme!r}, "
        f"all_themes={options.all_themes}, format={options.output_format!r}, lang={language!r}",
        flush=True,
    )

    thread = threading.Thread(
        target=run_generation_job,
        args=(job.id, options, client_host),
        daemon=True,
        name=f"maptoposter-job-{job.id[:8]}",
    )
    thread.start()
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
    elif requested_job.status == "running":
        initial_job_id = requested_job.id
        if message is None:
            message = requested_job.message
    elif requested_job.status == "succeeded":
        if message is None:
            message = requested_job.message
        if focus_name is None and requested_job.generated_names:
            focus_name = requested_job.generated_names[0]
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
