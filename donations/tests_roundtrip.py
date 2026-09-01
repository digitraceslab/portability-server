"""End-to-end round trip for the ingest and retrieval pipeline.

Generates source data at class setup, runs it through extraction and encrypted
storage, reads it back through the paged seams the API uses, and compares the
reassembled result against the generated original.

The test asserts only the contract - which data types exist, how many rows they
have, and what the pages contain - and deliberately touches no storage
internals, so that it keeps its meaning when the storage format changes.

The generated data is large and lives in a temporary working directory that is
removed at teardown; nothing is written to the repository. Set
``ROUND_TRIP_ROWS`` to generate a heavier run than the default.
"""
import os
import shutil
import tempfile
import zipfile

import numpy as np
import pandas as pd
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from donations.models import GoogleDonation, ResearcherToken
from donations.utils import crypto

TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()

LARGE_ROWS = int(os.environ.get("ROUND_TRIP_ROWS", 25_000))
SMALL_ROWS = 5
UNSORTED_ROWS = 300

PAGE = 1000  # the cap the researcher API enforces


def _generate_sources(directory):
    """Write one CSV per data type and return them keyed by data type.

    Three shapes are covered deliberately: a type with only a few rows, one
    large enough to be split when stored, and one with a different set of
    columns whose rows are not in time order.
    """
    rng = np.random.default_rng(20260901)
    sources = {}

    sources["app_usage"] = pd.DataFrame({
        "timestamp": pd.date_range("2024-03-01", periods=SMALL_ROWS, freq="h"),
        "app": ["mail", "maps", "browser", "mail", "camera"],
        "seconds": [12, 340, 5, 78, 900],
    })

    sources["location_history"] = pd.DataFrame({
        "timestamp": pd.date_range("2014-01-01", periods=LARGE_ROWS, freq="5s"),
        "activity": rng.choice(["still", "walking", "running", "in_vehicle"], LARGE_ROWS),
        "lat": rng.random(LARGE_ROWS),
        "lon": rng.random(LARGE_ROWS),
    })

    unsorted_ts = pd.date_range("2020-01-01", periods=UNSORTED_ROWS, freq="min").to_numpy().copy()
    rng.shuffle(unsorted_ts)
    sources["search_queries"] = pd.DataFrame({
        "timestamp": unsorted_ts,
        "query": ["".join(rng.choice(list("abcdefg "), 20)) for _ in range(UNSORTED_ROWS)],
        "locale": rng.choice(["fi", "en", "sv"], UNSORTED_ROWS),
        "results": rng.integers(0, 500, UNSORTED_ROWS),
    })

    for data_type, frame in sources.items():
        frame.to_csv(os.path.join(directory, f"{data_type}.csv"), index=False)
    return sources


def _reader_for(expected_columns):
    """Build a source reader that claims a part only if its columns match.

    Readers are handed a decrypted copy of a downloaded part with no name, so
    they identify their own data by shape, as the real readers do by structure.
    """
    def read(path):
        frame = pd.read_csv(path, parse_dates=["timestamp"])
        if set(frame.columns) != set(expected_columns):
            return None
        return frame
    return read


@override_settings(ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class RoundTripTest(TestCase):
    """Source data in, per-type storage, paged reads out, compared to the original."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._previous_cwd = os.getcwd()
        cls._workdir = tempfile.mkdtemp(prefix="roundtrip-")
        os.chdir(cls._workdir)

        cls.source_dir = os.path.join(cls._workdir, "sources")
        os.makedirs(cls.source_dir)
        cls.sources = _generate_sources(cls.source_dir)
        cls.data_types = sorted(cls.sources)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls._previous_cwd)
        shutil.rmtree(cls._workdir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.researcher = ResearcherToken.objects.create(name="round-trip")
        self.donation = GoogleDonation.objects.create(
            researcher=self.researcher,
            requested_data_types=list(self.data_types),
        )
        self.donation.processing_status = "processing"

        # Each generated CSV stands in for one downloaded part, stored the way
        # a real download is stored.
        parts_dir = os.path.join(self._workdir, "parts")
        os.makedirs(parts_dir, exist_ok=True)
        for data_type in self.data_types:
            source = os.path.join(self.source_dir, f"{data_type}.csv")
            stored = os.path.join(parts_dir, f"{data_type}.part")
            shutil.copyfile(source, stored)
            self.donation.downloaded_files.append(stored)
        self.donation.save()

        readers = {
            data_type: _reader_for(frame.columns)
            for data_type, frame in self.sources.items()
        }
        self._original_readers = GoogleDonation.DATA_TYPE_READERS
        GoogleDonation.DATA_TYPE_READERS = readers
        self.addCleanup(
            setattr, GoogleDonation, "DATA_TYPE_READERS", self._original_readers
        )

        self.donation.extract_and_process()
        self.donation.refresh_from_db()

    def _page_through(self, data_type, page_size):
        """Read every page in sequence and return the concatenated rows."""
        rows = []
        offset = 0
        while True:
            page = self.donation.fetch_data(data_type, limit=page_size, offset=offset)
            if not page:
                break
            rows.extend(page)
            offset += page_size
        return rows

    def _expected(self, data_type):
        """The generated rows as stored: in time order, timestamps in ms."""
        frame = self.sources[data_type].sort_values("timestamp", kind="stable").copy()
        frame["timestamp"] = frame["timestamp"].astype("datetime64[ms]").astype("int64")
        return frame.to_dict("records")

    def _compare(self, data_type, rows):
        expected = self._expected(data_type)
        self.assertEqual(len(rows), len(expected), f"row count for {data_type}")
        columns = list(self.sources[data_type].columns)
        for position, (got, want) in enumerate(zip(rows, expected)):
            for column in columns:
                if isinstance(want[column], float):
                    # CSV storage is lossy in the last digit of a double. The
                    # tolerance can go once the data is stored in a binary
                    # format, which round-trips exactly.
                    self.assertAlmostEqual(
                        got[column], want[column], places=12,
                        msg=f"{data_type} row {position} column {column}",
                    )
                else:
                    self.assertEqual(
                        got[column], want[column],
                        f"{data_type} row {position} column {column}",
                    )

    def test_all_data_types_are_stored(self):
        self.assertEqual(sorted(self.donation.get_data_types()), self.data_types)

    def test_row_counts_match_the_source(self):
        for data_type in self.data_types:
            self.assertEqual(
                self.donation.count_rows(data_type),
                len(self.sources[data_type]),
                f"count_rows for {data_type}",
            )

    def test_pages_reassemble_to_the_source(self):
        for data_type in self.data_types:
            self._compare(data_type, self._page_through(data_type, PAGE))

    def test_page_size_not_dividing_the_data(self):
        # A page boundary that does not line up with any storage boundary.
        self._compare("search_queries", self._page_through("search_queries", 7))
        self._compare("search_queries", self._page_through("search_queries", 299))

    def test_unsorted_source_is_stored_in_time_order(self):
        source = list(self.sources["search_queries"]["timestamp"])
        self.assertNotEqual(source, sorted(source), "generated data should be unsorted")

        rows = self._page_through("search_queries", PAGE)
        stored = [row["timestamp"] for row in rows]
        self.assertEqual(stored, sorted(stored), "stored data is ordered by time")
        self._compare("search_queries", rows)

    def test_ordering_lets_a_date_filter_skip_row_groups(self):
        frame = self.sources["search_queries"].sort_values("timestamp")
        start = frame["timestamp"].iloc[-5]
        rows = self.donation.fetch_data(
            "search_queries", limit=PAGE, start_date=start,
        )
        self.assertEqual(len(rows), 5)

    def test_final_partial_page(self):
        total = len(self.sources["search_queries"])
        offset = total - 3
        page = self.donation.fetch_data("search_queries", limit=PAGE, offset=offset)
        self.assertEqual(len(page), 3)

    def test_limit_larger_than_the_remaining_rows(self):
        total = len(self.sources["app_usage"])
        page = self.donation.fetch_data("app_usage", limit=PAGE, offset=0)
        self.assertEqual(len(page), total)

    def test_offset_at_and_beyond_the_end(self):
        total = len(self.sources["app_usage"])
        self.assertEqual(self.donation.fetch_data("app_usage", limit=PAGE, offset=total), [])
        self.assertEqual(
            self.donation.fetch_data("app_usage", limit=PAGE, offset=total + 10), []
        )

    def test_date_filter_selects_a_subrange(self):
        frame = self.sources["location_history"]
        start = frame["timestamp"].iloc[len(frame) // 2]
        end = frame["timestamp"].iloc[-1]
        expected = int(((frame["timestamp"] >= start) & (frame["timestamp"] <= end)).sum())

        self.assertEqual(
            self.donation.count_rows("location_history", start_date=start, end_date=end),
            expected,
        )
        page = self.donation.fetch_data(
            "location_history", limit=PAGE, offset=0, start_date=start, end_date=end
        )
        self.assertEqual(page[0]["timestamp"], int(start.value // 10**6))


@override_settings(ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class MultipleArchiveTest(TestCase):
    """A data type continued across archives, delivered as zips.

    A source splits a large data type when an archive reaches its maximum
    size, so the same type arrives in more than one file and has to be
    stitched back together.
    """

    ROWS_PER_ARCHIVE = 400
    DATA_TYPE = "location_history"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._previous_cwd = os.getcwd()
        cls._workdir = tempfile.mkdtemp(prefix="multi-archive-")
        os.chdir(cls._workdir)

        cls.source = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=cls.ROWS_PER_ARCHIVE * 3, freq="min"),
            "activity": ["still", "walking", "running"] * cls.ROWS_PER_ARCHIVE,
        })

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls._previous_cwd)
        shutil.rmtree(cls._workdir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.researcher = ResearcherToken.objects.create(name="multi-archive")
        self.donation = GoogleDonation.objects.create(
            researcher=self.researcher, requested_data_types=[self.DATA_TYPE],
        )
        self.donation.processing_status = "processing"

        # Three archives, each carrying a slice of the same data type.
        for index in range(3):
            chunk = self.source.iloc[
                index * self.ROWS_PER_ARCHIVE:(index + 1) * self.ROWS_PER_ARCHIVE
            ]
            archive = os.path.join(self._workdir, f"export-{index}.zip")
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(f"{self.DATA_TYPE}.csv", chunk.to_csv(index=False))
            self.donation.downloaded_files.append(archive)
        self.donation.save()

        def read_from_zip(path):
            with zipfile.ZipFile(path) as bundle:
                name = f"{self.DATA_TYPE}.csv"
                if name not in bundle.namelist():
                    return None
                with bundle.open(name) as member:
                    return pd.read_csv(member, parse_dates=["timestamp"])

        original = GoogleDonation.DATA_TYPE_READERS
        GoogleDonation.DATA_TYPE_READERS = {self.DATA_TYPE: read_from_zip}
        self.addCleanup(setattr, GoogleDonation, "DATA_TYPE_READERS", original)

        self.donation.extract_and_process()
        self.donation.refresh_from_db()

    def test_every_row_from_every_archive_is_kept(self):
        self.assertEqual(self.donation.count_rows(self.DATA_TYPE), len(self.source))

    def test_pages_reassemble_across_archives(self):
        rows = []
        offset = 0
        while True:
            page = self.donation.fetch_data(self.DATA_TYPE, limit=250, offset=offset)
            if not page:
                break
            rows.extend(page)
            offset += 250
        self.assertEqual([row["activity"] for row in rows], list(self.source["activity"]))

    def test_archives_are_combined_into_one_file(self):
        self.assertTrue(os.path.exists(self.donation._combined_path(self.DATA_TYPE)))
        self.assertEqual(self.donation._archive_paths(self.DATA_TYPE), [])
        self.assertEqual(len(self.donation._data_paths(self.DATA_TYPE)), 1)

    def test_archives_are_deleted_after_processing(self):
        for archive in self.donation.downloaded_files:
            self.assertFalse(os.path.exists(archive))
