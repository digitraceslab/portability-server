"""Donations whose source data arrives as archives.

Sources differ in how archives arrive and how their contents are read, but not
in how the result is stored or served: one encrypted Parquet file per data type
per archive, combined into one file per type when the export is complete, and
read back a page at a time.
"""
import os

from django.utils import timezone

from donations.utils import parquet_store


class ArchiveDonationMixin:
    """Reading archives into per-data-type storage, and serving pages of it.

    Subclasses provide ``storage_name``, ``archive_field`` and
    ``DATA_TYPE_READERS``; they differ only in how archives arrive and how
    their contents are read.
    """

    #: Subdirectory under the donation's data directory.
    storage_name = None
    #: Name of the model field listing archives to read.
    archive_field = None

    # -- paths ------------------------------------------------------------

    def _data_dir(self, data_type):
        return f'data/{self.pk}/{self.storage_name}/{data_type}'

    def _combined_path(self, data_type):
        """The single file a data type settles into once the export is done."""
        return f'{self._data_dir(data_type)}/combined.parquet'

    def _archive_path(self, data_type, archive_index):
        """The file holding one archive's rows for a data type."""
        return f'{self._data_dir(data_type)}/part-{archive_index:04d}.parquet'

    def _archive_paths(self, data_type):
        directory = self._data_dir(data_type)
        if not os.path.isdir(directory):
            return []
        return sorted(
            os.path.join(directory, name)
            for name in os.listdir(directory)
            if name.startswith('part-') and name.endswith('.parquet')
        )

    def _data_paths(self, data_type):
        """Files holding a data type: the combined one, or the per-archive set."""
        combined = self._combined_path(data_type)
        if os.path.exists(combined):
            return [combined]
        return self._archive_paths(data_type)

    # -- reading ----------------------------------------------------------

    def fetch_data(self, data_type, limit=1000, start_date=None, end_date=None, offset=0):
        """A page of stored rows. Empty only when there is nothing to return."""
        if data_type not in self.get_data_types():
            return []
        paths = self._data_paths(data_type)
        if not paths:
            return []
        frame = parquet_store.read_rows(
            paths, limit=limit, offset=offset,
            start_date=start_date, end_date=end_date,
        )
        if frame.empty:
            return []
        # Milliseconds
        frame = frame.copy()
        frame['timestamp'] = frame['timestamp'].astype('datetime64[ms]').astype('int64')
        return frame.to_dict('records')

    def count_rows(self, data_type, start_date=None, end_date=None):
        if data_type not in self.get_data_types():
            return 0
        paths = self._data_paths(data_type)
        if not paths:
            return 0
        return parquet_store.count_rows(paths, start_date, end_date)

    # -- writing ----------------------------------------------------------

    def _device_id(self):
        return str(self.pk)

    def _expected_data_types(self):
        return [
            data_type for data_type in self.requested_data_types
            if data_type in self.DATA_TYPE_READERS
        ]

    def _archives(self):
        return getattr(self, self.archive_field) or []

    def _read_archives(self):
        """Read each archive once, writing every expected data type it holds.

        An archive is self-contained: its rows for a data type go to a file of
        their own, so a type continued in a later archive simply gets another
        file. Returns True when every archive has been dealt with.
        """
        file_status = self.file_status or {}
        data_type_status = self.data_type_status or {}
        expected = self._expected_data_types()

        for archive_index, filepath in enumerate(self._archives()):
            if file_status.get(filepath, {}).get('processed'):
                continue
            if not os.path.exists(filepath):
                self.processing_log += f"File not found: {filepath}\n"
                file_status[filepath] = {'processed': True, 'skipped': True}
                continue

            try:
                for data_type in expected:
                    self._touch_archive(filepath)
                    frame = self._read_data_type(filepath, data_type, filepath)
                    if frame is None:
                        continue
                    path = self._archive_path(data_type, archive_index)
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    parquet_store.write_frames(path, [frame])
                    data_type_status[data_type] = {
                        'received': True,
                        'received_at': timezone.now().isoformat(),
                    }
                    self.processing_log += f"Received {data_type} from {filepath}\n"
            finally:
                self._discard_archive(filepath)

            file_status[filepath] = {
                'processed': True,
                'processed_at': timezone.now().isoformat(),
            }
            self.file_status = file_status
            self.data_type_status = data_type_status
            self.save()

        return all(filepath in file_status for filepath in self._archives())

    def _touch_archive(self, path):
        """Mark an archive as still in use.

        Reading a file does not update its modification time, so a cleanup
        that collects abandoned archives by age would eventually collect one
        that is still being read. Touching it between data types keeps the
        timestamp advancing while work is happening, and stops advancing the
        moment the worker dies.
        """
        try:
            os.utime(path, None)
        except OSError:
            pass

    def _discard_archive(self, path):
        """Remove an archive once it has been read, or once reading failed.

        Archives are held unencrypted while they are being processed, so they
        are not kept afterwards. Continuing after a failure means fetching the
        archive again rather than reusing a copy left on disk.
        """
        try:
            os.remove(path)
        except OSError:
            pass

    def _read_data_type(self, archive_path, data_type, source_name):
        """Rows of one data type from one archive, or None if it holds none."""
        import pandas as pd

        reader = self.DATA_TYPE_READERS[data_type]
        try:
            frame = reader(archive_path)
        except NotImplementedError:
            return None
        except Exception as e:
            self.processing_log += (
                f"Failed to read {data_type} from {source_name}: {e}\n"
            )
            return None
        if isinstance(frame, list):
            frame = pd.DataFrame(frame)
        if frame is None or frame.empty:
            return None
        frame = frame.reset_index()
        frame['device_id'] = self._device_id()
        return frame

    def _combine_archives(self, data_types=None):
        """Merge each data type's per-archive files into a single file."""
        for data_type in data_types or self._expected_data_types():
            parts = self._archive_paths(data_type)
            if not parts:
                continue
            combined = self._combined_path(data_type)
            if len(parts) == 1:
                os.replace(parts[0], combined)
                continue
            staging = f'{combined}.tmp'
            parquet_store.combine(parts, staging)
            os.replace(staging, combined)
            for part in parts:
                os.remove(part)
