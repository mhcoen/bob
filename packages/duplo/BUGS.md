# Bugs

## doc_tables.py:256 -- `dt` can be `None` from `zip_longest`, causing `AttributeError` crash
**Severity**: high
In `_extract_dl`, `itertools.zip_longest(terms, defs)` pads the shorter list with `None`. The code handles `dd` being `None` on line 257 (`if dd else ""`), but `dt` is used unconditionally on line 256: `dt.get_text(separator=" ").strip()`. If a `<dl>` has more `<dd>` elements than `<dt>` elements, `dt` will be `None` and this crashes with `AttributeError: 'NoneType' object has no attribute 'get_text'`.

## video_extractor.py:128-131 -- Stale frames from prior runs included in results
**Severity**: medium
After ffmpeg runs, frames are collected by globbing `output_dir` for files matching the `{stem}_scene_` prefix. The `-y` flag only overwrites files with identical names, so if a previous run for the same video produced more frames than the current run, the extra old frames remain on disk and are included in the glob results. For example, if the first run extracted 10 frames and a retry with a different threshold extracts 5, frames 6-10 from the prior run are still returned.

## video_extractor.py:101 -- `%` in video filename misinterpreted by ffmpeg output pattern
**Severity**: medium
The output pattern `str(output_dir / f"{stem}_scene_%04d.png")` interpolates the video file's stem directly. If the filename contains `%` characters (e.g., `intro%20video.mp4`), ffmpeg interprets `%` as its own format specifier in the output path, which will cause ffmpeg to fail or produce files at unexpected paths. The `%04d` in the pattern is the intended format specifier, but any `%` in `stem` corrupts the pattern.

## saver.py:739 -- `Path.rename()` fails across filesystem boundaries
**Severity**: medium
`move_references` uses `src.rename(dest)` which calls `os.rename` under the hood. This raises `OSError` (Invalid cross-device link) when the source and destination are on different filesystems or mount points. The similar function `store_accepted_frames` (line 667) correctly uses `shutil.copy2` for the same kind of operation.

## screenshotter.py:40-52 -- Browser process not closed if `new_page()` or navigation fails with exception
**Severity**: low
If `browser.new_page()` raises an exception after `p.chromium.launch()` succeeds, execution jumps past `browser.close()` on line 52. The `with sync_playwright()` context manager cleans up the playwright connection, but the spawned Chromium browser process may not be terminated cleanly, leaving an orphan process.
