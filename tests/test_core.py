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
