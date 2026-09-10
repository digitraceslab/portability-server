"""Tests for donations.utils.archive_names."""
from django.test import SimpleTestCase

from donations.utils.archive_names import generated_archive_name


class GeneratedArchiveNameTests(SimpleTestCase):
    def test_keeps_a_normal_extension(self):
        name = generated_archive_name(42, 'export.csv')
        self.assertTrue(name.startswith('42_'))
        self.assertTrue(name.endswith('.csv'))
        self.assertNotIn('export', name)

    def test_lowercases_the_extension(self):
        name = generated_archive_name(1, 'archive.ZIP')
        self.assertTrue(name.endswith('.zip'))

    def test_strips_path_separators(self):
        name = generated_archive_name(1, '../../etc/passwd')
        self.assertNotIn('/', name)
        self.assertNotIn('..', name)
        self.assertNotIn('etc', name)
        self.assertNotIn('passwd', name)

    def test_strips_unicode_names(self):
        name = generated_archive_name(1, 'éèê.txt')
        self.assertTrue(name.endswith('.txt'))
        self.assertNotIn('é', name)

    def test_handles_no_extension(self):
        name = generated_archive_name(1, 'noextension')
        self.assertEqual(name.count('.'), 0)

    def test_drops_oversized_extension(self):
        name = generated_archive_name(1, 'file.toolongext')
        self.assertFalse(name.endswith('.toolongext'))
        self.assertNotIn('toolongext', name)

    def test_names_are_unique(self):
        first = generated_archive_name(1, 'export.csv')
        second = generated_archive_name(1, 'export.csv')
        self.assertNotEqual(first, second)
