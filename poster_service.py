#!/usr/bin/env python3
"""
Shared poster generation services used by the CLI and the web app.
"""

from __future__ import annotations

import asyncio
import json
import os
import pickle
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

BASE_DIR = Path(__file__).resolve().parent


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    if not value:
        return default

    path = Path(value)
    if path.is_absolute():
        return path
    return (BASE_DIR / path).resolve()


os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault(
    "MPLCONFIGDIR", str(_path_from_env("MPLCONFIGDIR", BASE_DIR / "cache" / "matplotlib"))
)

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
from geopandas import GeoDataFrame
from geopy.geocoders import Nominatim
from lat_lon_parser import parse
from matplotlib.font_manager import FontProperties
from networkx import MultiDiGraph
from shapely.geometry import Point
from tqdm import tqdm

from font_management import load_fonts


class CacheError(Exception):
    """Raised when a cache operation fails."""


@dataclass(frozen=True)
class PosterOptions:
    """Validated input for poster generation."""

    city: str
    country: str
    theme: str = "terracotta"
    all_themes: bool = False
    distance: int = 18000
    width: float = 12.0
    height: float = 16.0
    country_label: str | None = None
    display_city: str | None = None
    display_country: str | None = None
    font_family: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    output_format: str = "png"


THEMES_DIR = BASE_DIR / "themes"
DEFAULT_POSTERS_DIR = BASE_DIR / "posters"
DEFAULT_CACHE_DIR = BASE_DIR / "cache"
VALID_OUTPUT_FORMATS = {"png", "svg", "pdf"}
MAX_DIMENSION_INCHES = 20.0
GENERATION_LOCK = threading.Lock()


CACHE_DIR = _path_from_env("CACHE_DIR", DEFAULT_CACHE_DIR)
POSTERS_DIR = _path_from_env("POSTERS_DIR", DEFAULT_POSTERS_DIR)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
POSTERS_DIR.mkdir(parents=True, exist_ok=True)
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)


def _cache_path(key: str) -> Path:
    safe = key.replace(os.sep, "_")
    return CACHE_DIR / f"{safe}.pkl"


def cache_get(key: str) -> Any | None:
    try:
        path = _cache_path(key)
        if not path.exists():
            return None
        with path.open("rb") as handle:
            return pickle.load(handle)
    except Exception as exc:
        raise CacheError(f"Cache read failed: {exc}") from exc


def cache_set(key: str, value: Any) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(key)
        with path.open("wb") as handle:
            pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:
        raise CacheError(f"Cache write failed: {exc}") from exc


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()
    return stripped or None


def parse_coordinate(value: str | None) -> float | None:
    cleaned = normalize_optional_text(value)
    if cleaned is None:
        return None

    try:
        return float(parse(cleaned))
    except Exception as exc:
        raise ValueError(f"Invalid coordinate value: {value}") from exc


def normalize_options(options: PosterOptions) -> PosterOptions:
    normalized = replace(
        options,
        city=options.city.strip(),
        country=options.country.strip(),
        theme=(options.theme or "terracotta").strip() or "terracotta",
        country_label=normalize_optional_text(options.country_label),
        display_city=normalize_optional_text(options.display_city),
        display_country=normalize_optional_text(options.display_country),
        font_family=normalize_optional_text(options.font_family),
        output_format=options.output_format.lower().strip(),
    )

    if not normalized.city:
        raise ValueError("City is required.")
    if not normalized.country:
        raise ValueError("Country is required.")

    if normalized.distance <= 0:
        raise ValueError("Distance must be greater than 0.")
    if normalized.width <= 0:
        raise ValueError("Width must be greater than 0.")
    if normalized.height <= 0:
        raise ValueError("Height must be greater than 0.")

    if normalized.output_format not in VALID_OUTPUT_FORMATS:
        raise ValueError(
            "Format must be one of: png, svg, pdf."
        )

    has_lat = normalized.latitude is not None
    has_lon = normalized.longitude is not None
    if has_lat != has_lon:
        raise ValueError("Latitude and longitude must be provided together.")

    return replace(
        normalized,
        width=min(normalized.width, MAX_DIMENSION_INCHES),
        height=min(normalized.height, MAX_DIMENSION_INCHES),
    )


def is_latin_script(text: str | None) -> bool:
    if not text:
        return True

    latin_count = 0
    total_alpha = 0

    for char in text:
        if char.isalpha():
            total_alpha += 1
            if ord(char) < 0x250:
                latin_count += 1

    if total_alpha == 0:
        return True

    return (latin_count / total_alpha) > 0.8


def generate_output_filename(city: str, theme_name: str, output_format: str) -> Path:
    POSTERS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    city_slug = city.lower().replace(" ", "_")
    filename = f"{city_slug}_{theme_name}_{timestamp}.{output_format.lower()}"
    return POSTERS_DIR / filename


def get_available_themes() -> list[str]:
    if not THEMES_DIR.exists():
        return []
    return sorted(path.stem for path in THEMES_DIR.glob("*.json"))


def get_theme_catalog() -> list[dict[str, str]]:
    themes = []
    for theme_name in get_available_themes():
        theme_path = THEMES_DIR / f"{theme_name}.json"
        display_name = theme_name
        description = ""

        try:
            with theme_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
                display_name = data.get("name", theme_name)
                description = data.get("description", "")
        except (OSError, json.JSONDecodeError):
            pass

        themes.append(
            {
                "name": theme_name,
                "display_name": display_name,
                "description": description,
            }
        )

    return themes


def load_theme(theme_name: str = "terracotta") -> dict[str, str]:
    theme_file = THEMES_DIR / f"{theme_name}.json"
    if not theme_file.exists():
        print(
            f"Theme file '{theme_file}' not found. Falling back to terracotta defaults."
        )
        return {
            "name": "Terracotta",
            "description": "Mediterranean warmth - burnt orange and clay tones on cream",
            "bg": "#F5EDE4",
            "text": "#8B4513",
            "gradient_color": "#F5EDE4",
            "water": "#A8C4C4",
            "parks": "#E8E0D0",
            "road_motorway": "#A0522D",
            "road_primary": "#B8653A",
            "road_secondary": "#C9846A",
            "road_tertiary": "#D9A08A",
            "road_residential": "#E5C4B0",
            "road_default": "#D9A08A",
        }

    with theme_file.open("r", encoding="utf-8") as handle:
        theme = json.load(handle)
        print(f"Loaded theme: {theme.get('name', theme_name)}")
        if "description" in theme:
            print(f"  {theme['description']}")
        return cast(dict[str, str], theme)


def create_gradient_fade(ax, color: str, location: str = "bottom", zorder: int = 10) -> None:
    vals = np.linspace(0, 1, 256).reshape(-1, 1)
    gradient = np.hstack((vals, vals))

    rgb = mcolors.to_rgb(color)
    my_colors = np.zeros((256, 4))
    my_colors[:, 0] = rgb[0]
    my_colors[:, 1] = rgb[1]
    my_colors[:, 2] = rgb[2]

    if location == "bottom":
        my_colors[:, 3] = np.linspace(1, 0, 256)
        extent_y_start = 0
        extent_y_end = 0.25
    else:
        my_colors[:, 3] = np.linspace(0, 1, 256)
        extent_y_start = 0.75
        extent_y_end = 1.0

    custom_cmap = mcolors.ListedColormap(my_colors)

    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    y_range = ylim[1] - ylim[0]

    y_bottom = ylim[0] + y_range * extent_y_start
    y_top = ylim[0] + y_range * extent_y_end

    ax.imshow(
        gradient,
        extent=[xlim[0], xlim[1], y_bottom, y_top],
        aspect="auto",
        cmap=custom_cmap,
        zorder=zorder,
        origin="lower",
    )


def get_edge_colors_by_type(g: MultiDiGraph, theme: dict[str, str]) -> list[str]:
    edge_colors = []
    for _u, _v, data in g.edges(data=True):
        highway = data.get("highway", "unclassified")

        if isinstance(highway, list):
            highway = highway[0] if highway else "unclassified"

        if highway in ["motorway", "motorway_link"]:
            color = theme["road_motorway"]
        elif highway in ["trunk", "trunk_link", "primary", "primary_link"]:
            color = theme["road_primary"]
        elif highway in ["secondary", "secondary_link"]:
            color = theme["road_secondary"]
        elif highway in ["tertiary", "tertiary_link"]:
            color = theme["road_tertiary"]
        elif highway in ["residential", "living_street", "unclassified"]:
            color = theme["road_residential"]
        else:
            color = theme["road_default"]

        edge_colors.append(color)

    return edge_colors


def get_edge_widths_by_type(g: MultiDiGraph) -> list[float]:
    edge_widths = []
    for _u, _v, data in g.edges(data=True):
        highway = data.get("highway", "unclassified")

        if isinstance(highway, list):
            highway = highway[0] if highway else "unclassified"

        if highway in ["motorway", "motorway_link"]:
            width = 1.2
        elif highway in ["trunk", "trunk_link", "primary", "primary_link"]:
            width = 1.0
        elif highway in ["secondary", "secondary_link"]:
            width = 0.8
        elif highway in ["tertiary", "tertiary_link"]:
            width = 0.6
        else:
            width = 0.4

        edge_widths.append(width)

    return edge_widths


def get_coordinates(city: str, country: str) -> tuple[float, float]:
    coords_key = f"coords_{city.lower()}_{country.lower()}"
    cached = cache_get(coords_key)
    if cached:
        print(f"Using cached coordinates for {city}, {country}")
        return cast(tuple[float, float], cached)

    print("Looking up coordinates...")
    geolocator = Nominatim(user_agent="city_map_poster", timeout=10)
    time.sleep(1)

    try:
        location = geolocator.geocode(f"{city}, {country}")
    except Exception as exc:
        raise ValueError(f"Geocoding failed for {city}, {country}: {exc}") from exc

    if asyncio.iscoroutine(location):
        try:
            location = asyncio.run(location)
        except RuntimeError as exc:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                raise RuntimeError(
                    "Geocoder returned a coroutine while an event loop is already running."
                ) from exc
            location = loop.run_until_complete(location)

    if location:
        print(f"Found coordinates: {location.latitude}, {location.longitude}")
        try:
            cache_set(coords_key, (location.latitude, location.longitude))
        except CacheError as exc:
            print(exc)
        return (location.latitude, location.longitude)

    raise ValueError(f"Could not find coordinates for {city}, {country}")


def get_crop_limits(
    g_proj: MultiDiGraph,
    center_lat_lon: tuple[float, float],
    fig,
    dist: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    lat, lon = center_lat_lon
    center = ox.projection.project_geometry(
        Point(lon, lat), crs="EPSG:4326", to_crs=g_proj.graph["crs"]
    )[0]
    center_x, center_y = center.x, center.y

    fig_width, fig_height = fig.get_size_inches()
    aspect = fig_width / fig_height

    half_x = dist
    half_y = dist

    if aspect > 1:
        half_y = half_x / aspect
    else:
        half_x = half_y * aspect

    return (
        (center_x - half_x, center_x + half_x),
        (center_y - half_y, center_y + half_y),
    )


def fetch_graph(point: tuple[float, float], dist: float) -> MultiDiGraph | None:
    lat, lon = point
    cache_key = f"graph_{lat}_{lon}_{dist}"
    cached = cache_get(cache_key)
    if cached is not None:
        print("Using cached street network")
        return cast(MultiDiGraph, cached)

    try:
        graph = ox.graph_from_point(
            point,
            dist=dist,
            dist_type="bbox",
            network_type="all",
            truncate_by_edge=True,
        )
        time.sleep(0.5)
        try:
            cache_set(cache_key, graph)
        except CacheError as exc:
            print(exc)
        return graph
    except Exception as exc:
        print(f"OSMnx error while fetching graph: {exc}")
        return None


def fetch_features(
    point: tuple[float, float],
    dist: float,
    tags: dict[str, Any],
    name: str,
) -> GeoDataFrame | None:
    lat, lon = point
    tag_str = "_".join(tags.keys())
    cache_key = f"{name}_{lat}_{lon}_{dist}_{tag_str}"
    cached = cache_get(cache_key)
    if cached is not None:
        print(f"Using cached {name}")
        return cast(GeoDataFrame, cached)

    try:
        data = ox.features_from_point(point, tags=tags, dist=dist)
        time.sleep(0.3)
        try:
            cache_set(cache_key, data)
        except CacheError as exc:
            print(exc)
        return data
    except Exception as exc:
        print(f"OSMnx error while fetching features: {exc}")
        return None


def create_poster(
    city: str,
    country: str,
    point: tuple[float, float],
    dist: int,
    output_file: Path,
    output_format: str,
    theme: dict[str, str],
    width: float = 12,
    height: float = 16,
    country_label: str | None = None,
    name_label: str | None = None,
    display_city: str | None = None,
    display_country: str | None = None,
    fonts: dict[str, str] | None = None,
) -> None:
    display_city = display_city or name_label or city
    display_country = display_country or country_label or country

    print(f"\nGenerating map for {city}, {country}...")

    with tqdm(
        total=3,
        desc="Fetching map data",
        unit="step",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}",
    ) as progress:
        progress.set_description("Downloading street network")
        compensated_dist = dist * (max(height, width) / min(height, width)) / 4
        graph = fetch_graph(point, compensated_dist)
        if graph is None:
            raise RuntimeError("Failed to retrieve street network data.")
        progress.update(1)

        progress.set_description("Downloading water features")
        water = fetch_features(
            point,
            compensated_dist,
            tags={"natural": ["water", "bay", "strait"], "waterway": "riverbank"},
            name="water",
        )
        progress.update(1)

        progress.set_description("Downloading parks/green spaces")
        parks = fetch_features(
            point,
            compensated_dist,
            tags={"leisure": "park", "landuse": "grass"},
            name="parks",
        )
        progress.update(1)

    print("All data retrieved successfully.")
    print("Rendering map...")
    fig, ax = plt.subplots(figsize=(width, height), facecolor=theme["bg"])
    ax.set_facecolor(theme["bg"])
    ax.set_position((0.0, 0.0, 1.0, 1.0))

    projected_graph = ox.project_graph(graph)

    if water is not None and not water.empty:
        water_polys = water[water.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not water_polys.empty:
            try:
                water_polys = ox.projection.project_gdf(water_polys)
            except Exception:
                water_polys = water_polys.to_crs(projected_graph.graph["crs"])
            water_polys.plot(
                ax=ax, facecolor=theme["water"], edgecolor="none", zorder=0.5
            )

    if parks is not None and not parks.empty:
        parks_polys = parks[parks.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not parks_polys.empty:
            try:
                parks_polys = ox.projection.project_gdf(parks_polys)
            except Exception:
                parks_polys = parks_polys.to_crs(projected_graph.graph["crs"])
            parks_polys.plot(
                ax=ax, facecolor=theme["parks"], edgecolor="none", zorder=0.8
            )

    edge_colors = get_edge_colors_by_type(projected_graph, theme)
    edge_widths = get_edge_widths_by_type(projected_graph)
    crop_xlim, crop_ylim = get_crop_limits(projected_graph, point, fig, compensated_dist)

    ox.plot_graph(
        projected_graph,
        ax=ax,
        bgcolor=theme["bg"],
        node_size=0,
        edge_color=edge_colors,
        edge_linewidth=edge_widths,
        show=False,
        close=False,
    )
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(crop_xlim)
    ax.set_ylim(crop_ylim)

    create_gradient_fade(ax, theme["gradient_color"], location="bottom", zorder=10)
    create_gradient_fade(ax, theme["gradient_color"], location="top", zorder=10)

    scale_factor = min(height, width) / 12.0
    base_main = 60
    base_sub = 22
    base_coords = 14
    base_attr = 8

    active_fonts = fonts or load_fonts()
    if active_fonts:
        font_sub = FontProperties(
            fname=active_fonts["light"], size=base_sub * scale_factor
        )
        font_coords = FontProperties(
            fname=active_fonts["regular"], size=base_coords * scale_factor
        )
        font_attr = FontProperties(
            fname=active_fonts["light"], size=base_attr * scale_factor
        )
    else:
        font_sub = FontProperties(
            family="monospace", weight="normal", size=base_sub * scale_factor
        )
        font_coords = FontProperties(
            family="monospace", size=base_coords * scale_factor
        )
        font_attr = FontProperties(family="monospace", size=base_attr * scale_factor)

    if is_latin_script(display_city):
        spaced_city = "  ".join(list(display_city.upper()))
    else:
        spaced_city = display_city

    base_adjusted_main = base_main * scale_factor
    city_char_count = len(display_city)
    if city_char_count > 10:
        length_factor = 10 / city_char_count
        adjusted_font_size = max(base_adjusted_main * length_factor, 10 * scale_factor)
    else:
        adjusted_font_size = base_adjusted_main

    if active_fonts:
        font_main_adjusted = FontProperties(
            fname=active_fonts["bold"], size=adjusted_font_size
        )
    else:
        font_main_adjusted = FontProperties(
            family="monospace", weight="bold", size=adjusted_font_size
        )

    ax.text(
        0.5,
        0.14,
        spaced_city,
        transform=ax.transAxes,
        color=theme["text"],
        ha="center",
        fontproperties=font_main_adjusted,
        zorder=11,
    )

    ax.text(
        0.5,
        0.10,
        display_country.upper(),
        transform=ax.transAxes,
        color=theme["text"],
        ha="center",
        fontproperties=font_sub,
        zorder=11,
    )

    lat, lon = point
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "W"
    coords = f"{abs(lat):.4f} {lat_dir} / {abs(lon):.4f} {lon_dir}"

    ax.text(
        0.5,
        0.07,
        coords,
        transform=ax.transAxes,
        color=theme["text"],
        alpha=0.7,
        ha="center",
        fontproperties=font_coords,
        zorder=11,
    )

    ax.plot(
        [0.4, 0.6],
        [0.125, 0.125],
        transform=ax.transAxes,
        color=theme["text"],
        linewidth=1 * scale_factor,
        zorder=11,
    )

    ax.text(
        0.98,
        0.02,
        "Copyright OpenStreetMap contributors",
        transform=ax.transAxes,
        color=theme["text"],
        alpha=0.5,
        ha="right",
        va="bottom",
        fontproperties=font_attr,
        zorder=11,
    )

    print(f"Saving to {output_file}...")
    save_kwargs: dict[str, Any] = {
        "facecolor": theme["bg"],
        "bbox_inches": "tight",
        "pad_inches": 0.05,
    }

    if output_format.lower() == "png":
        save_kwargs["dpi"] = 300

    plt.savefig(output_file, format=output_format.lower(), **save_kwargs)
    plt.close()
    print(f"Poster saved as {output_file}")


def resolve_fonts(font_family: str | None) -> dict[str, str] | None:
    fonts = load_fonts(font_family)
    if fonts is None and font_family:
        print(f"Falling back to local Roboto fonts after failing to load '{font_family}'.")
        return load_fonts()
    return fonts


def resolve_coordinates(options: PosterOptions) -> tuple[float, float]:
    if options.latitude is not None and options.longitude is not None:
        return (options.latitude, options.longitude)
    return get_coordinates(options.city, options.country)


def generate_posters(options: PosterOptions) -> list[Path]:
    normalized = normalize_options(options)
    available_themes = get_available_themes()
    if not available_themes:
        raise ValueError("No themes found in the themes directory.")

    if normalized.all_themes:
        themes_to_generate = available_themes
    else:
        if normalized.theme not in available_themes:
            raise ValueError(
                f"Theme '{normalized.theme}' not found. Available themes: {', '.join(available_themes)}"
            )
        themes_to_generate = [normalized.theme]

    fonts = resolve_fonts(normalized.font_family)
    coordinates = resolve_coordinates(normalized)
    generated_files: list[Path] = []

    with GENERATION_LOCK:
        for theme_name in themes_to_generate:
            theme = load_theme(theme_name)
            output_file = generate_output_filename(
                normalized.city, theme_name, normalized.output_format
            )
            create_poster(
                normalized.city,
                normalized.country,
                coordinates,
                normalized.distance,
                output_file,
                normalized.output_format,
                theme,
                normalized.width,
                normalized.height,
                country_label=normalized.country_label,
                display_city=normalized.display_city,
                display_country=normalized.display_country,
                fonts=fonts,
            )
            generated_files.append(output_file)

    return generated_files


def format_bytes(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


def list_generated_files() -> list[dict[str, Any]]:
    POSTERS_DIR.mkdir(parents=True, exist_ok=True)
    valid_suffixes = {".png", ".pdf", ".svg"}
    files = []
    for path in POSTERS_DIR.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in valid_suffixes:
            continue

        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "path": path,
                "size_bytes": stat.st_size,
                "size_label": format_bytes(stat.st_size),
                "modified_at": datetime.fromtimestamp(stat.st_mtime),
                "modified_label": datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        )

    files.sort(key=lambda item: item["modified_at"], reverse=True)
    return files


def get_safe_poster_path(filename: str) -> Path | None:
    if Path(filename).name != filename:
        return None

    root = POSTERS_DIR.resolve()
    candidate = (POSTERS_DIR / filename).resolve()

    try:
        candidate.relative_to(root)
    except ValueError:
        return None

    if not candidate.is_file():
        return None

    return candidate
