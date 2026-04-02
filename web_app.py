"""
FastAPI application for generating and downloading map posters.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
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


def build_form_data(overrides: dict[str, str] | None = None) -> dict[str, str]:
    form = {
        "city": "",
        "country": "",
        "theme": "terracotta",
        "distance": "18000",
        "width": "12",
        "height": "16",
        "format": "png",
        "latitude": "",
        "longitude": "",
        "country_label": "",
        "display_city": "",
        "display_country": "",
        "font_family": "",
        "all_themes": "",
    }
    if overrides:
        form.update(overrides)
    return form


def parse_int(value: str, field_name: str) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a whole number.") from exc
    return parsed


def parse_float(value: str, field_name: str) -> float:
    try:
        parsed = float(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a number.") from exc
    return parsed


def render_index(
    request: Request,
    *,
    message: str | None = None,
    error: str | None = None,
    form_data: dict[str, str] | None = None,
    focus_name: str | None = None,
) -> HTMLResponse:
    posters = enrich_posters(request, list_generated_files())
    context = {
        "request": request,
        "message": message,
        "error": error,
        "form": build_form_data(form_data),
        "themes": get_theme_catalog(),
        "posters": posters,
        "featured_poster": choose_featured_poster(posters, focus_name),
    }
    return templates.TemplateResponse("index.html", context)


def redirect_to_index(
    request: Request,
    *,
    message: str | None = None,
    error: str | None = None,
    focus_name: str | None = None,
) -> RedirectResponse:
    query: dict[str, str] = {}
    if message:
        query["message"] = message
    if error:
        query["error"] = error
    if focus_name:
        query["focus"] = focus_name

    url = str(request.url_for("index"))
    if query:
        url = f"{url}?{urlencode(query)}"

    return RedirectResponse(url=url, status_code=303)


def wants_json_response(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/json" in accept.lower()


def enrich_posters(request: Request, posters: list[dict[str, object]]) -> list[dict[str, object]]:
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
                "preview_url": str(request.url_for("preview_poster", filename=name)),
                "open_url": str(request.url_for("preview_poster", filename=name)),
                "download_url": str(request.url_for("download_poster", filename=name)),
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


def build_generate_payload(request: Request, generated_names: set[str]) -> dict[str, object]:
    posters = enrich_posters(request, list_generated_files())
    generated = [poster for poster in posters if poster["name"] in generated_names]
    return {"posters": posters, "generated": generated}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return render_index(
        request,
        message=request.query_params.get("message"),
        error=request.query_params.get("error"),
        focus_name=request.query_params.get("focus"),
    )


@app.get("/generate")
async def generate_redirect(request: Request) -> RedirectResponse:
    return redirect_to_index(request)


@app.post("/generate", response_model=None)
async def generate(
    request: Request,
    city: str = Form(""),
    country: str = Form(""),
    theme: str = Form("terracotta"),
    distance: str = Form("18000"),
    width: str = Form("12"),
    height: str = Form("16"),
    output_format: str = Form("png", alias="format"),
    latitude: str = Form(""),
    longitude: str = Form(""),
    country_label: str = Form(""),
    display_city: str = Form(""),
    display_country: str = Form(""),
    font_family: str = Form(""),
    all_themes: str = Form(""),
) -> RedirectResponse | JSONResponse:
    try:
        options = PosterOptions(
            city=city,
            country=country,
            theme=theme,
            all_themes=all_themes == "on",
            distance=parse_int(distance, "Distance"),
            width=parse_float(width, "Width"),
            height=parse_float(height, "Height"),
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
        if wants_json_response(request):
            payload = build_generate_payload(request, set())
            return JSONResponse(
                {"ok": False, "error": str(exc), **payload},
                status_code=400,
            )
        return redirect_to_index(request, error=str(exc))
    except Exception as exc:
        if wants_json_response(request):
            payload = build_generate_payload(request, set())
            return JSONResponse(
                {"ok": False, "error": f"Poster generation failed: {exc}", **payload},
                status_code=500,
            )
        return redirect_to_index(request, error=f"Poster generation failed: {exc}")

    names = ", ".join(path.name for path in generated)
    generated_names = {path.name for path in generated}
    message = f"Created {len(generated)} file(s): {names}"

    if wants_json_response(request):
        payload = build_generate_payload(request, generated_names)
        return JSONResponse({"ok": True, "message": message, **payload})

    focus_name = generated[0].name if generated else None
    return redirect_to_index(request, message=message, focus_name=focus_name)


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
