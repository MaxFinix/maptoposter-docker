"""
FastAPI application for generating and downloading map posters.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from poster_service import (
    PosterOptions,
    generate_posters,
    get_safe_poster_path,
    get_theme_catalog,
    list_generated_files,
    parse_coordinate,
)


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app = FastAPI(title="MaptoPoster", version="0.3.0")


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
) -> HTMLResponse:
    context = {
        "request": request,
        "message": message,
        "error": error,
        "form": build_form_data(form_data),
        "themes": get_theme_catalog(),
        "posters": list_generated_files(),
    }
    return templates.TemplateResponse("index.html", context)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return render_index(request)


@app.post("/generate", response_class=HTMLResponse)
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
) -> HTMLResponse:
    form_data = build_form_data(
        {
            "city": city,
            "country": country,
            "theme": theme,
            "distance": distance,
            "width": width,
            "height": height,
            "format": output_format,
            "latitude": latitude,
            "longitude": longitude,
            "country_label": country_label,
            "display_city": display_city,
            "display_country": display_country,
            "font_family": font_family,
            "all_themes": all_themes,
        }
    )

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
        return render_index(request, error=str(exc), form_data=form_data)
    except Exception as exc:
        return render_index(
            request,
            error=f"Poster generation failed: {exc}",
            form_data=form_data,
        )

    names = ", ".join(path.name for path in generated)
    return render_index(
        request,
        message=f"Created {len(generated)} file(s): {names}",
        form_data=form_data,
    )


@app.get("/download/{filename}")
async def download_poster(filename: str) -> FileResponse:
    path = get_safe_poster_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = int(os.environ.get("APP_PORT", "8000"))
    uvicorn.run("web_app:app", host=host, port=port)
