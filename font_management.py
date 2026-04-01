"""
Font management helpers.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import requests


BASE_DIR = Path(__file__).resolve().parent
LOCAL_FONTS_DIR = BASE_DIR / "fonts"
UNRAID_PERMISSION_HINT = (
    'On Unraid, remove `user: "99:100"` from compose.yaml or prepare the mounted '
    "folders with write access for UID 99 / GID 100."
)


class FontLoadError(RuntimeError):
    """Raised when a requested font cannot be cached safely."""


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    if not value:
        return default

    path = Path(value)
    if path.is_absolute():
        return path
    return (BASE_DIR / path).resolve()


FONTS_CACHE_DIR = _path_from_env("FONTS_CACHE_DIR", LOCAL_FONTS_DIR / "cache")


def get_default_fonts() -> dict[str, str]:
    return {
        "bold": str(LOCAL_FONTS_DIR / "Roboto-Bold.ttf"),
        "regular": str(LOCAL_FONTS_DIR / "Roboto-Regular.ttf"),
        "light": str(LOCAL_FONTS_DIR / "Roboto-Light.ttf"),
    }


def download_google_font(
    font_family: str, weights: list[int] | None = None
) -> Optional[dict[str, str]]:
    if weights is None:
        weights = [300, 400, 700]

    try:
        FONTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FontLoadError(
            f"Font cache path '{FONTS_CACHE_DIR}' is not writable. {UNRAID_PERMISSION_HINT}"
        ) from exc

    font_name_safe = font_family.replace(" ", "_").lower()
    font_files: dict[str, str] = {}

    try:
        weights_str = ";".join(map(str, weights))
        api_url = "https://fonts.googleapis.com/css2"
        params = {"family": f"{font_family}:wght@{weights_str}"}
        headers = {"User-Agent": "Mozilla/5.0"}

        response = requests.get(api_url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        css_content = response.text

        weight_url_map: dict[int, str] = {}
        font_face_blocks = re.split(r"@font-face\s*\{", css_content)
        for block in font_face_blocks[1:]:
            weight_match = re.search(r"font-weight:\s*(\d+)", block)
            if not weight_match:
                continue

            weight = int(weight_match.group(1))
            url_match = re.search(r"url\((https://[^)]+\.(woff2|ttf))\)", block)
            if url_match:
                weight_url_map[weight] = url_match.group(1)

        weight_map = {300: "light", 400: "regular", 700: "bold"}
        for weight in weights:
            weight_key = weight_map.get(weight, "regular")
            weight_url = weight_url_map.get(weight)

            if not weight_url and weight_url_map:
                closest_weight = min(weight_url_map.keys(), key=lambda item: abs(item - weight))
                weight_url = weight_url_map[closest_weight]
                print(
                    f"  Using weight {closest_weight} for {weight_key} (requested {weight} not available)"
                )

            if not weight_url:
                continue

            file_ext = "woff2" if weight_url.endswith(".woff2") else "ttf"
            font_filename = f"{font_name_safe}_{weight_key}.{file_ext}"
            font_path = FONTS_CACHE_DIR / font_filename

            if not font_path.exists():
                print(f"  Downloading {font_family} {weight_key} ({weight})...")
                try:
                    font_response = requests.get(weight_url, timeout=10)
                    font_response.raise_for_status()
                    font_path.write_bytes(font_response.content)
                except OSError as exc:
                    raise FontLoadError(
                        f"Font cache path '{FONTS_CACHE_DIR}' is not writable. "
                        f"Failed while saving '{font_filename}'. {UNRAID_PERMISSION_HINT}"
                    ) from exc
                except Exception as exc:
                    print(f"  Failed to download {weight_key}: {exc}")
                    continue
            else:
                print(f"  Using cached {font_family} {weight_key}")

            font_files[weight_key] = str(font_path)

        if "regular" not in font_files and font_files:
            first_key = next(iter(font_files))
            font_files["regular"] = font_files[first_key]
            print(f"  Using {first_key} weight as regular")

        if "bold" not in font_files and "regular" in font_files:
            font_files["bold"] = font_files["regular"]
            print("  Using regular weight as bold")
        if "light" not in font_files and "regular" in font_files:
            font_files["light"] = font_files["regular"]
            print("  Using regular weight as light")

        return font_files if font_files else None
    except FontLoadError:
        raise
    except Exception as exc:
        print(f"Error downloading Google Font '{font_family}': {exc}")
        return None


def load_fonts(font_family: Optional[str] = None) -> Optional[dict[str, str]]:
    if font_family and font_family.lower() != "roboto":
        print(f"Loading Google Font: {font_family}")
        fonts = download_google_font(font_family)
        if fonts:
            print(f"Font '{font_family}' loaded successfully")
            return fonts
        print(f"Failed to load '{font_family}', falling back to local Roboto")

    fonts = get_default_fonts()
    for path in fonts.values():
        if not os.path.exists(path):
            print(f"Font not found: {path}")
            return None

    return fonts
