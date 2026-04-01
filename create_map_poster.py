#!/usr/bin/env python3
"""
City Map Poster Generator CLI entrypoint.
"""

from __future__ import annotations

import argparse
import sys
import traceback

from poster_service import (
    MAX_DIMENSION_INCHES,
    PosterOptions,
    generate_posters,
    get_theme_catalog,
    parse_coordinate,
)


def print_examples() -> None:
    print(
        """
City Map Poster Generator
=========================

Usage:
  python create_map_poster.py --city <city> --country <country> [options]

Examples:
  python create_map_poster.py -c "Paris" -C "France"
  python create_map_poster.py -c "New York" -C "USA" -t noir -d 12000
  python create_map_poster.py --list-themes
        """.strip()
    )


def list_themes() -> None:
    catalog = get_theme_catalog()
    if not catalog:
        print("No themes found in the themes directory.")
        return

    print("\nAvailable Themes:")
    print("-" * 60)
    for theme in catalog:
        print(f"  {theme['name']}")
        print(f"    {theme['display_name']}")
        if theme["description"]:
            print(f"    {theme['description']}")
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate beautiful map posters for any city",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python create_map_poster.py --city "New York" --country "USA"
  python create_map_poster.py --city "Paris" --country "France" --theme noir --distance 15000
  python create_map_poster.py --list-themes
        """,
    )

    parser.add_argument("--city", "-c", type=str, help="City name")
    parser.add_argument("--country", "-C", type=str, help="Country name")
    parser.add_argument(
        "--latitude", "-lat", dest="latitude", type=str, help="Override latitude center point"
    )
    parser.add_argument(
        "--longitude",
        "-long",
        dest="longitude",
        type=str,
        help="Override longitude center point",
    )
    parser.add_argument(
        "--country-label",
        dest="country_label",
        type=str,
        help="Override country text displayed on poster",
    )
    parser.add_argument(
        "--theme",
        "-t",
        type=str,
        default="terracotta",
        help="Theme name (default: terracotta)",
    )
    parser.add_argument(
        "--all-themes",
        dest="all_themes",
        action="store_true",
        help="Generate posters for all themes",
    )
    parser.add_argument(
        "--distance",
        "-d",
        type=int,
        default=18000,
        help="Map radius in meters (default: 18000)",
    )
    parser.add_argument(
        "--width",
        "-W",
        type=float,
        default=12,
        help="Image width in inches (default: 12, max: 20)",
    )
    parser.add_argument(
        "--height",
        "-H",
        type=float,
        default=16,
        help="Image height in inches (default: 16, max: 20)",
    )
    parser.add_argument("--list-themes", action="store_true", help="List all available themes")
    parser.add_argument(
        "--display-city",
        "-dc",
        type=str,
        help="Custom display name for city",
    )
    parser.add_argument(
        "--display-country",
        "-dC",
        type=str,
        help="Custom display name for country",
    )
    parser.add_argument(
        "--font-family",
        type=str,
        help='Google Fonts family name (for example "Noto Sans JP")',
    )
    parser.add_argument(
        "--format",
        "-f",
        default="png",
        choices=["png", "svg", "pdf"],
        help="Output format for the poster (default: png)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = sys.argv[1:] if argv is None else argv
    parser = build_parser()

    if not args_list:
        print_examples()
        return 0

    args = parser.parse_args(args_list)

    if args.list_themes:
        list_themes()
        return 0

    if not args.city or not args.country:
        print("Error: --city and --country are required.\n")
        print_examples()
        return 1

    width = args.width
    height = args.height
    if width > MAX_DIMENSION_INCHES:
        print(
            f"Width {width} exceeds the maximum allowed limit of {MAX_DIMENSION_INCHES}. Using {MAX_DIMENSION_INCHES} instead."
        )
        width = MAX_DIMENSION_INCHES
    if height > MAX_DIMENSION_INCHES:
        print(
            f"Height {height} exceeds the maximum allowed limit of {MAX_DIMENSION_INCHES}. Using {MAX_DIMENSION_INCHES} instead."
        )
        height = MAX_DIMENSION_INCHES

    try:
        options = PosterOptions(
            city=args.city,
            country=args.country,
            theme=args.theme,
            all_themes=args.all_themes,
            distance=args.distance,
            width=width,
            height=height,
            country_label=args.country_label,
            display_city=args.display_city,
            display_country=args.display_country,
            font_family=args.font_family,
            latitude=parse_coordinate(args.latitude),
            longitude=parse_coordinate(args.longitude),
            output_format=args.format,
        )

        print("=" * 50)
        print("City Map Poster Generator")
        print("=" * 50)
        generated_files = generate_posters(options)
        print("\n" + "=" * 50)
        print("Poster generation complete!")
        for path in generated_files:
            print(f"- {path}")
        print("=" * 50)
        return 0
    except Exception as exc:
        print(f"\nError: {exc}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
