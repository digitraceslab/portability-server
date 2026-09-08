"""Tests for the lazy row source used by the data preview."""
from django.test import SimpleTestCase

from donations.utils.paging import DonationRows


class FakeDonation:
    """Records what is asked of it and serves rows from a list."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def count_rows(self, data_type, start_date=None, end_date=None):
        self.calls.append(("count", data_type, start_date, end_date))
        return len(self.rows)

    def fetch_data(self, data_type, limit=1000, start_date=None, end_date=None, offset=0):
        self.calls.append(("fetch", limit, offset, start_date, end_date))
        return self.rows[offset:offset + limit]


class DonationRowsTests(SimpleTestCase):
    def setUp(self):
        self.donation = FakeDonation([{"n": i} for i in range(120)])
        self.rows = DonationRows(self.donation, "search", start_date="2024-01-01")

    def test_length_comes_from_count_and_is_cached(self):
        self.assertEqual(len(self.rows), 120)
        self.assertEqual(len(self.rows), 120)
        self.assertEqual(self.donation.calls, [("count", "search", "2024-01-01", None)])

    def test_slice_fetches_only_that_window(self):
        page = self.rows[50:100]
        self.assertEqual([r["n"] for r in page], list(range(50, 100)))
        self.assertIn(("fetch", 50, 50, "2024-01-01", None), self.donation.calls)

    def test_empty_slice_reads_nothing(self):
        self.assertEqual(self.rows[10:10], [])
        self.assertFalse(any(c[0] == "fetch" for c in self.donation.calls))

    def test_paginator_reads_one_page(self):
        from django.core.paginator import Paginator
        page = Paginator(self.rows, 50).get_page(3)
        self.assertEqual([r["n"] for r in page.object_list], list(range(100, 120)))
        self.assertEqual(page.paginator.num_pages, 3)
        fetches = [c for c in self.donation.calls if c[0] == "fetch"]
        self.assertEqual(fetches, [("fetch", 20, 100, "2024-01-01", None)])

    def test_indexing_is_rejected(self):
        with self.assertRaises(TypeError):
            self.rows[3]
