"""
FastAPI application for generating and downloading map posters.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
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
    }
    return templates.TemplateResponse("index.html", context)


def redirect_to_index(
    request: Request,
    *,
    language: str,
    message: str | None = None,
    error: str | None = None,
    focus_name: str | None = None,
) -> RedirectResponse:
    query: dict[str, str] = {"lang": language}
    if message:
        query["message"] = message
    if error:
        query["error"] = error
    if focus_name:
        query["focus"] = focus_name

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
    response = render_index(
        request,
        language=language,
        message=request.query_params.get("message"),
        error=request.query_params.get("error"),
        focus_name=request.query_params.get("focus"),
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

    print(
        "Generate request started "
        f"from {client_host}: city={city!r}, country={country!r}, theme={theme!r}, "
        f"all_themes={all_themes == 'on'}, format={output_format!r}, lang={language!r}",
        flush=True,
    )

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
        generated = await run_in_threadpool(generate_posters, options)
    except ValueError as exc:
        localized_error = translate_error_message(str(exc), language)
        print(
            f"Generate request validation failed from {client_host}: {localized_error}",
            flush=True,
        )
        if wants_json_response(request):
            payload = build_generate_payload(request, set(), language)
            response = JSONResponse(
                {"ok": False, "error": localized_error, **payload},
                status_code=400,
            )
            return with_language_cookie(response, language)
        response = redirect_to_index(request, language=language, error=localized_error)
        return with_language_cookie(response, language)
    except Exception as exc:
        localized_error = build_generation_failure_message(
            translate_error_message(str(exc), language),
            language,
        )
        print(
            f"Generate request failed from {client_host}: {exc}",
            flush=True,
        )
        if wants_json_response(request):
            payload = build_generate_payload(request, set(), language)
            response = JSONResponse(
                {"ok": False, "error": localized_error, **payload},
                status_code=500,
            )
            return with_language_cookie(response, language)
        response = redirect_to_index(request, language=language, error=localized_error)
        return with_language_cookie(response, language)

    names = ", ".join(path.name for path in generated)
    generated_names = {path.name for path in generated}
    message = format_created_message(len(generated), names, language)
    print(
        f"Generate request completed from {client_host}: {len(generated)} file(s) -> {names}",
        flush=True,
    )

    if wants_json_response(request):
        payload = build_generate_payload(request, generated_names, language)
        response = JSONResponse({"ok": True, "message": message, **payload})
        return with_language_cookie(response, language)

    focus_name = generated[0].name if generated else None
    response = redirect_to_index(
        request,
        language=language,
        message=message,
        focus_name=focus_name,
    )
    return with_language_cookie(response, language)


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
