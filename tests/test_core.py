"""Tests minimaux pour la CI."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image
from PIL.ExifTags import IFD

from pycture.duplicates import find_duplicates
from pycture.exif_utils import get_capture_info, parse_datetime_from_filename
from pycture.merge import MergeOptions, build_merge_plan, execute_merge_plan
from pycture.organizer import (
    DuplicateAction,
    FolderStructure,
    OrganizerOptions,
    build_plan,
    organization_base,
    scan_inventory,
)
from pycture.photoslibrary import export_photos_library, is_photos_library


def test_parse_datetime_from_filename() -> None:
    assert parse_datetime_from_filename("2005-08-15 14-30-22.jpg") == datetime(
        2005, 8, 15, 14, 30, 22
    )
    assert parse_datetime_from_filename("IMG_20050815_143022.jpg") == datetime(
        2005, 8, 15, 14, 30, 22
    )
    assert parse_datetime_from_filename("vacances.jpg") is None


def test_datetime_original_preferred_over_datetime() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pycture_test_"))
    try:
        path = tmp / "photo.jpg"
        img = Image.new("RGB", (16, 16), (1, 2, 3))
        exif = img.getexif()
        exif[306] = "2000:01:01 00:00:00"
        ifd = exif.get_ifd(IFD.Exif)
        ifd[36867] = "2003:10:29 20:55:28"
        ifd[36868] = "2003:10:29 20:55:28"
        img.save(path, exif=exif)

        info = get_capture_info(path)
        assert info.source == "exif_original"
        assert info.value == datetime(2003, 10, 29, 20, 55, 28)
    finally:
        shutil.rmtree(tmp)


def test_duplicates_same_content() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pycture_dup_"))
    try:
        a = tmp / "a.jpg"
        b = tmp / "sub" / "b.jpg"
        b.parent.mkdir()
        Image.new("RGB", (10, 10), (9, 9, 9)).save(a)
        shutil.copy(a, b)
        groups = find_duplicates([a, b])
        assert len(groups) == 1
        assert len(groups[0].paths) == 2
        assert groups[0].keeper in (a, b)
        assert len(groups[0].duplicates) == 1
    finally:
        shutil.rmtree(tmp)


def test_organization_base_year_folder() -> None:
    assert organization_base(Path("/tmp/Photos/2005"), None).name == "Photos"
    assert organization_base(Path("/tmp/Photos"), None).name == "Photos"


def test_build_plan_sans_exif_stays_in_year() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pycture_org_")).resolve()
    try:
        photos = tmp / "Photos"
        year = photos / "2005"
        year.mkdir(parents=True)
        f = year / "scan.jpg"
        Image.new("RGB", (8, 8), (3, 3, 3)).save(f)
        ts = datetime(2022, 11, 23, 20, 0, 0).timestamp()
        os.utime(f, (ts, ts))

        plan = build_plan(
            OrganizerOptions(
                source_dir=year,
                structure=FolderStructure.YEAR_MONTH_DAY,
                rename_with_datetime=True,
                duplicate_action=DuplicateAction.KEEP_BOTH,
                include_videos=False,
                clean_junk=False,
                sync_file_dates=False,
            )
        )
        assert any(m.reason == "sans_exif" for m in plan.moves)
        move = next(m for m in plan.moves if m.reason == "sans_exif")
        assert move.destination.parts[-2:] == ("_sans_exif", "scan.jpg")
    finally:
        shutil.rmtree(tmp)


def test_scan_inventory_counts() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pycture_inv_"))
    try:
        Image.new("RGB", (4, 4), (1, 1, 1)).save(tmp / "a.jpg")
        Image.new("RGB", (4, 4), (2, 2, 2)).save(tmp / "b.png")
        (tmp / "c.avi").write_bytes(b"RIFF....AVI ")
        stats = scan_inventory(tmp, include_videos=True)
        assert stats.photo_total == 2
        assert stats.video_total == 1
        assert stats.photos_by_ext[".jpg"] == 1
    finally:
        shutil.rmtree(tmp)


def test_photos_library_export_minimal() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pycture_lib_")).resolve()
    try:
        lib = tmp / "Test.photoslibrary"
        originals = lib / "originals" / "a"
        originals.mkdir(parents=True)
        db_dir = lib / "database"
        db_dir.mkdir()

        uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        Image.new("RGB", (6, 6), (5, 5, 5)).save(originals / f"{uuid}.jpg")

        db = db_dir / "Photos.sqlite"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE ZASSET (Z_PK INTEGER PRIMARY KEY, ZUUID TEXT);
            CREATE TABLE ZADDITIONALASSETATTRIBUTES (
              Z_PK INTEGER PRIMARY KEY, ZASSET INTEGER, ZORIGINALFILENAME TEXT
            );
            INSERT INTO ZASSET VALUES (1, 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE');
            INSERT INTO ZADDITIONALASSETATTRIBUTES VALUES (1, 1, 'Vacances.JPG');
            """
        )
        conn.commit()
        conn.close()

        assert is_photos_library(lib)
        dest = tmp / "export"
        result = export_photos_library(lib, dest, include_videos=False)
        assert len(result.copied) == 1
        assert result.copied[0].stem == "Vacances"
    finally:
        shutil.rmtree(tmp)


def test_merge_skip_same_content() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pycture_merge_skip_")).resolve()
    try:
        src = tmp / "src"
        dst = tmp / "dst" / "2005" / "08"
        src.mkdir()
        dst.mkdir(parents=True)
        Image.new("RGB", (8, 8), (1, 2, 3)).save(src / "a.jpg")
        shutil.copy(src / "a.jpg", dst / "already.jpg")

        plan = build_merge_plan(
            MergeOptions(source_dir=src, destination_dir=tmp / "dst", move=False)
        )
        assert len(plan.skipped) == 1
        assert plan.to_merge == []
    finally:
        shutil.rmtree(tmp)


def test_merge_rename_same_name_different_content() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pycture_merge_rename_")).resolve()
    try:
        src = tmp / "src"
        dst_root = tmp / "dst"
        sub = src / "2005" / "08"
        sub.mkdir(parents=True)
        (dst_root / "2005" / "08").mkdir(parents=True)
        Image.new("RGB", (8, 8), (1, 1, 1)).save(sub / "photo.jpg")
        Image.new("RGB", (8, 8), (9, 9, 9)).save(dst_root / "2005" / "08" / "photo.jpg")

        plan = build_merge_plan(
            MergeOptions(source_dir=src, destination_dir=dst_root, move=False)
        )
        assert len(plan.to_merge) == 1
        assert plan.to_merge[0].reason == "rename_conflict"
        assert plan.to_merge[0].destination.name == "photo_1.jpg"

        execute_merge_plan(plan, dry_run=False)
        assert (dst_root / "2005" / "08" / "photo.jpg").is_file()
        assert (dst_root / "2005" / "08" / "photo_1.jpg").is_file()
        assert (src / "2005" / "08" / "photo.jpg").is_file()  # copie : source intacte
    finally:
        shutil.rmtree(tmp)


def test_merge_copy_preserves_relative_tree() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pycture_merge_copy_")).resolve()
    try:
        src = tmp / "src"
        dst = tmp / "dst"
        (src / "2003" / "09").mkdir(parents=True)
        dst.mkdir()
        Image.new("RGB", (6, 6), (4, 5, 6)).save(src / "2003" / "09" / "x.jpg")

        plan = build_merge_plan(
            MergeOptions(source_dir=src, destination_dir=dst, move=False)
        )
        assert len(plan.to_merge) == 1
        assert plan.to_merge[0].destination == dst / "2003" / "09" / "x.jpg"

        execute_merge_plan(plan, dry_run=False)
        assert (dst / "2003" / "09" / "x.jpg").is_file()
        assert (src / "2003" / "09" / "x.jpg").is_file()
    finally:
        shutil.rmtree(tmp)


def test_merge_move_removes_source() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pycture_merge_move_")).resolve()
    try:
        src = tmp / "src"
        dst = tmp / "dst"
        src.mkdir()
        dst.mkdir()
        Image.new("RGB", (5, 5), (7, 7, 7)).save(src / "m.jpg")

        plan = build_merge_plan(
            MergeOptions(source_dir=src, destination_dir=dst, move=True)
        )
        execute_merge_plan(plan, dry_run=False)
        assert (dst / "m.jpg").is_file()
        assert not (src / "m.jpg").exists()
    finally:
        shutil.rmtree(tmp)


def test_merge_two_identical_sources_only_one_copied() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pycture_merge_dupsrc_")).resolve()
    try:
        src = tmp / "src"
        dst = tmp / "dst"
        (src / "a").mkdir(parents=True)
        (src / "b").mkdir()
        dst.mkdir()
        Image.new("RGB", (4, 4), (2, 2, 2)).save(src / "a" / "one.jpg")
        shutil.copy(src / "a" / "one.jpg", src / "b" / "two.jpg")

        plan = build_merge_plan(
            MergeOptions(source_dir=src, destination_dir=dst, move=False)
        )
        assert len(plan.to_merge) == 1
        assert len(plan.skipped) == 1
        execute_merge_plan(plan, dry_run=False)
        copied = list(dst.rglob("*.jpg"))
        assert len(copied) == 1
    finally:
        shutil.rmtree(tmp)
