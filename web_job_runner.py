"""
Background worker process for long-running poster generation jobs.
"""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path

from poster_service import POSTERS_DIR, PosterOptions, generate_posters, record_poster_history
from web_i18n import (
    build_generation_failure_message,
    format_created_message,
    format_duration_label,
    get_text_bundle,
    localize_progress_step,
    translate_error_message,
)


def write_status(status_file: Path, payload: dict[str, object]) -> None:
    status_file.parent.mkdir(parents=True, exist_ok=True)
    temp_path = status_file.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(status_file)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a MaptoPoster generation job.")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--options-file", required=True)
    args = parser.parse_args()

    language = args.language
    text = get_text_bundle(language)["messages"]
    status_file = Path(args.status_file)
    output_dir = Path(args.output_dir)
    options_file = Path(args.options_file)
    started_at = time.time()
    canceled = False
    current_step: str | None = None
    finalized_paths: list[Path] = []

    with options_file.open("r", encoding="utf-8") as handle:
        options = PosterOptions(**json.load(handle))

    status: dict[str, object] = {
        "job_id": args.job_id,
        "language": language,
        "status": "running",
        "message": text["job_started"],
        "error": None,
        "step": None,
        "generated_names": [],
        "created_at": started_at,
        "started_at": started_at,
        "finished_at": None,
        "updated_at": started_at,
        "can_cancel": True,
        "duration_seconds": None,
        "duration_label": None,
        "options": asdict(options),
    }

    def persist(**updates: object) -> None:
        status.update(updates)
        status["updated_at"] = time.time()
        write_status(status_file, status)

    def progress(step: str) -> None:
        nonlocal current_step
        current_step = localize_progress_step(step, language)
        persist(step=current_step)

    def handle_cancel(_signum: int, _frame) -> None:
        nonlocal canceled
        canceled = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_cancel)
    signal.signal(signal.SIGINT, handle_cancel)

    persist()

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        generated = generate_posters(
            options,
            output_dir=output_dir,
            progress_callback=progress,
        )

        progress("Finalizing files")
        POSTERS_DIR.mkdir(parents=True, exist_ok=True)
        final_names: list[str] = []
        for staged_file in generated:
            final_path = POSTERS_DIR / staged_file.name
            staged_file.replace(final_path)
            finalized_paths.append(final_path)
            final_names.append(final_path.name)

        finished_at = time.time()
        duration_seconds = max(0, int(round(finished_at - started_at)))
        duration_label = format_duration_label(duration_seconds, language)
        record_poster_history(
            final_names,
            job_id=args.job_id,
            duration_seconds=duration_seconds,
            created_at=started_at,
            finished_at=finished_at,
        )
        message = format_created_message(len(final_names), ", ".join(final_names), language)
        shutil.rmtree(output_dir, ignore_errors=True)
        persist(
            status="succeeded",
            message=message,
            error=None,
            step=localize_progress_step("Completed", language),
            generated_names=final_names,
            finished_at=finished_at,
            can_cancel=False,
            duration_seconds=duration_seconds,
            duration_label=duration_label,
        )
        return 0
    except KeyboardInterrupt:
        for final_path in finalized_paths:
            try:
                final_path.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(output_dir, ignore_errors=True)
        persist(
            status="canceled",
            message=text["job_canceled"],
            error=None,
            step=current_step,
            generated_names=[],
            finished_at=time.time(),
            can_cancel=False,
            duration_seconds=None,
            duration_label=None,
        )
        return 130 if canceled else 1
    except ValueError as exc:
        for final_path in finalized_paths:
            try:
                final_path.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(output_dir, ignore_errors=True)
        persist(
            status="failed",
            message=None,
            error=translate_error_message(str(exc), language),
            step=current_step,
            generated_names=[],
            finished_at=time.time(),
            can_cancel=False,
            duration_seconds=None,
            duration_label=None,
        )
        return 1
    except Exception as exc:
        for final_path in finalized_paths:
            try:
                final_path.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(output_dir, ignore_errors=True)
        persist(
            status="failed",
            message=None,
            error=build_generation_failure_message(
                translate_error_message(str(exc), language),
                language,
            ),
            step=current_step,
            generated_names=[],
            finished_at=time.time(),
            can_cancel=False,
            duration_seconds=None,
            duration_label=None,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
