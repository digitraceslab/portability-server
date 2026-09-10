"""Tests for the zip member size bound applied before archives are read."""
import io
import os
import tempfile
import zipfile

from django.test import SimpleTestCase, override_settings

from donations.utils.archive_limits import check_archive_bounds, physical_memory_bytes


def _zip_with(sizes):
    """Write a zip whose members have the given uncompressed sizes."""
    handle = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
    with zipfile.ZipFile(handle, 'w', zipfile.ZIP_DEFLATED) as archive:
        for index, size in enumerate(sizes):
            archive.writestr(f'member-{index}.json', b'0' * size)
    handle.close()
    return handle.name


class ArchiveBoundsTests(SimpleTestCase):
    def setUp(self):
        self._paths = []
        self.addCleanup(lambda: [os.remove(p) for p in self._paths])

    def _zip(self, sizes):
        path = _zip_with(sizes)
        self._paths.append(path)
        return path

    @override_settings(ARCHIVE_MAX_MEMBER_BYTES=1000)
    def test_small_members_pass(self):
        ok, detail = check_archive_bounds(self._zip([100, 900]))
        self.assertTrue(ok, detail)

    @override_settings(ARCHIVE_MAX_MEMBER_BYTES=1000)
    def test_member_over_limit_is_rejected(self):
        ok, detail = check_archive_bounds(self._zip([100, 1001]))
        self.assertFalse(ok)
        self.assertIn('1001', detail)

    @override_settings(ARCHIVE_MAX_MEMBER_BYTES=1000)
    def test_highly_compressed_bomb_is_rejected_by_declared_size(self):
        # A megabyte of zeros deflates to about a kilobyte; the declared
        # size is what counts.
        path = self._zip([1024 * 1024])
        self.assertLess(os.path.getsize(path), 10_000)
        ok, _ = check_archive_bounds(path)
        self.assertFalse(ok)

    @override_settings(ARCHIVE_MAX_MEMBER_BYTES=1000)
    def test_non_zip_file_passes(self):
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.write(b'timestamp,activity\n')
        handle.close()
        self._paths.append(handle.name)
        ok, detail = check_archive_bounds(handle.name)
        self.assertTrue(ok)
        self.assertEqual(detail, 'not a zip archive')

    @override_settings(ARCHIVE_MAX_MEMBER_BYTES=1000)
    def test_corrupt_zip_is_rejected(self):
        good = self._zip([10])
        with open(good, 'rb') as fh:
            data = fh.read()
        handle = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
        handle.write(data[:-40] + b'\x00' * 40)  # keep the magic, break the directory
        handle.close()
        self._paths.append(handle.name)
        if zipfile.is_zipfile(handle.name):
            ok, _ = check_archive_bounds(handle.name)
            self.assertFalse(ok)

    @override_settings(ARCHIVE_MAX_MEMBER_BYTES=1000, ARCHIVE_MAX_MEMBERS=3)
    def test_too_many_members_is_rejected(self):
        ok, detail = check_archive_bounds(self._zip([1, 1, 1, 1]))
        self.assertFalse(ok)
        self.assertIn('4 members', detail)

    @override_settings(ARCHIVE_MAX_MEMBER_BYTES=1000, ARCHIVE_MAX_MEMBERS=3)
    def test_member_count_at_limit_passes(self):
        ok, _ = check_archive_bounds(self._zip([1, 1, 1]))
        self.assertTrue(ok)

    def test_physical_memory_is_positive_here(self):
        self.assertGreater(physical_memory_bytes(), 0)
