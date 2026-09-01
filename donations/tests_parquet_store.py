"""Tests for the encrypted Parquet storage layer."""
import os
import shutil
import tempfile

import pandas as pd
from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from donations.utils import parquet_store as store

TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()


def _frame(start, rows, freq="h"):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=rows, freq=freq),
        "activity": ["still"] * rows,
        "value": [float(i) / 3 for i in range(rows)],
    })


@override_settings(ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class ParquetStoreTest(SimpleTestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="store-")
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)

    def _path(self, name="data.parquet"):
        return os.path.join(self.workdir, name)

    def test_round_trip(self):
        frame = _frame("2024-01-01", 10)
        path = self._path()
        self.assertEqual(store.write_frames(path, [frame]), 10)
        result = store.read_rows([path])
        pd.testing.assert_frame_equal(result, frame)

    def test_stored_file_is_not_readable_without_the_key(self):
        path = self._path()
        store.write_frames(path, [_frame("2024-01-01", 3)])
        with open(path, "rb") as handle:
            self.assertNotIn(b"still", handle.read())
        with override_settings(ENCRYPTION_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(Exception):
                store.read_rows([path])

    def test_tampering_is_detected(self):
        path = self._path()
        store.write_frames(path, [_frame("2024-01-01", 200)])
        with open(path, "r+b") as handle:
            handle.seek(os.path.getsize(path) // 2)
            handle.write(b"\x00\x01\x02\x03")
        with self.assertRaises(Exception):
            store.read_rows([path])

    def test_row_group_size_follows_row_width(self):
        import pyarrow as pa
        narrow = pa.Table.from_pandas(_frame("2024-01-01", 1000), preserve_index=False)
        wide = pa.Table.from_pandas(
            pd.DataFrame({
                "timestamp": pd.date_range("2024-01-01", periods=1000, freq="h"),
                "blob": ["x" * 2000] * 1000,
            }),
            preserve_index=False,
        )
        self.assertGreater(store.rows_per_group(narrow), store.rows_per_group(wide))
        self.assertGreaterEqual(store.rows_per_group(wide), store.MIN_ROWS_PER_GROUP)

    def test_paging_reassembles_across_row_groups(self):
        frame = _frame("2024-01-01", 500, freq="min")
        path = self._path()
        original = store.ROW_GROUP_TARGET_BYTES
        store.ROW_GROUP_TARGET_BYTES = 4096  # force several groups from little data
        try:
            store.write_frames(path, [frame])
        finally:
            store.ROW_GROUP_TARGET_BYTES = original
        self.assertGreater(store.open_file(path).metadata.num_row_groups, 1)

        for page in (7, 100, 499):
            rows = []
            offset = 0
            while True:
                got = store.read_rows([path], limit=page, offset=offset)
                if got.empty:
                    break
                rows.append(got)
                offset += page
            pd.testing.assert_frame_equal(
                pd.concat(rows, ignore_index=True), frame, check_dtype=False
            )

    def test_paging_across_several_files(self):
        first, second = self._path("a.parquet"), self._path("b.parquet")
        store.write_frames(first, [_frame("2024-01-01", 5)])
        store.write_frames(second, [_frame("2024-02-01", 4)])
        paths = [first, second]

        self.assertEqual(store.count_rows(paths), 9)
        page = store.read_rows(paths, limit=6, offset=2)
        self.assertEqual(len(page), 6)
        self.assertEqual(
            page["timestamp"].iloc[0], pd.Timestamp("2024-01-01 02:00:00")
        )

    def test_offset_past_the_end_is_empty(self):
        path = self._path()
        store.write_frames(path, [_frame("2024-01-01", 5)])
        self.assertTrue(store.read_rows([path], limit=10, offset=5).empty)
        self.assertTrue(store.read_rows([path], limit=10, offset=50).empty)

    def test_date_filter_counts_and_reads(self):
        path = self._path()
        frame = _frame("2024-01-01", 48)
        store.write_frames(path, [frame])
        start, end = "2024-01-01 10:00:00", "2024-01-01 20:00:00"

        expected = frame[
            (frame["timestamp"] >= pd.Timestamp(start))
            & (frame["timestamp"] <= pd.Timestamp(end))
        ]
        self.assertEqual(store.count_rows([path], start, end), len(expected))
        rows = store.read_rows([path], limit=1000, start_date=start, end_date=end)
        self.assertEqual(len(rows), len(expected))
        self.assertEqual(rows["timestamp"].iloc[0], expected["timestamp"].iloc[0])

    def test_filter_matching_nothing(self):
        path = self._path()
        store.write_frames(path, [_frame("2024-01-01", 5)])
        self.assertEqual(store.count_rows([path], "2030-01-01", "2030-12-31"), 0)
        self.assertTrue(
            store.read_rows([path], start_date="2030-01-01", end_date="2030-12-31").empty
        )

    def test_combine_merges_files(self):
        first, second = self._path("a.parquet"), self._path("b.parquet")
        store.write_frames(first, [_frame("2024-01-01", 5)])
        store.write_frames(second, [_frame("2024-02-01", 4)])
        merged = self._path("merged.parquet")

        self.assertEqual(store.combine([first, second], merged), 9)
        pd.testing.assert_frame_equal(
            store.read_rows([merged]),
            store.read_rows([first, second]),
            check_dtype=False,
        )

    def test_write_in_batches_matches_one_frame(self):
        batched, single = self._path("batched.parquet"), self._path("single.parquet")
        whole = _frame("2024-01-01", 30)
        store.write_frames(batched, [whole.iloc[:10], whole.iloc[10:20], whole.iloc[20:]])
        store.write_frames(single, [whole])
        pd.testing.assert_frame_equal(
            store.read_rows([batched]), store.read_rows([single]), check_dtype=False
        )
