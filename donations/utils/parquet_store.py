"""Encrypted Parquet storage for processed donation data.

One file per data type per archive while an export is being ingested, combined
into a single file per type when it finishes. Row groups bound how much has to
be decrypted to answer a request: a page reads the one group that holds it, and
a date filter skips groups whose recorded timestamp range cannot match.

Encryption is Parquet Modular Encryption with an encrypted footer, so metadata
and statistics are unreadable without the key and nothing is parsed before it
has been authenticated. Parquet's data keys are wrapped with the service's own
``ENCRYPTION_KEY``; no key management service is involved.
"""
from datetime import timedelta

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.parquet.encryption as pe
from django.conf import settings

from donations.utils import crypto

#: Target size of a row group. The row count is derived from this and the
#: observed row width, so types with wide rows get fewer rows per group.
ROW_GROUP_TARGET_BYTES = 4 * 1024 * 1024

#: A row group always holds at least one row, even one wider than the target.
MIN_ROWS_PER_GROUP = 1

_FOOTER_KEY_ID = "donation-data"
_KEY_CACHE_LIFETIME = timedelta(minutes=5)

TIMESTAMP_COLUMN = "timestamp"


class _LocalKms(pe.KmsClient):
    """Wraps Parquet's data keys with the service's own encryption key."""

    def __init__(self, config):
        pe.KmsClient.__init__(self)
        self._fernet = crypto._get_fernet()

    def wrap_key(self, key_bytes, master_key_id):
        return self._fernet.encrypt(key_bytes).decode()

    def unwrap_key(self, wrapped_key, master_key_id):
        return self._fernet.decrypt(wrapped_key.encode())


def _factory():
    return pe.CryptoFactory(lambda config: _LocalKms(config))


def _kms_config():
    # The master key never leaves the service; this only names it.
    return pe.KmsConnectionConfig(custom_kms_conf={_FOOTER_KEY_ID: ""})


def _encryption_properties():
    return _factory().file_encryption_properties(
        _kms_config(),
        pe.EncryptionConfiguration(
            footer_key=_FOOTER_KEY_ID,
            uniform_encryption=True,
            cache_lifetime=_KEY_CACHE_LIFETIME,
            data_key_length_bits=256,
        ),
    )


def _decryption_properties():
    return _factory().file_decryption_properties(
        _kms_config(), pe.DecryptionConfiguration(cache_lifetime=_KEY_CACHE_LIFETIME)
    )


def rows_per_group(table):
    """Row count for a group, from this table's average row width."""
    if table.num_rows == 0:
        return MIN_ROWS_PER_GROUP
    average = max(1, table.nbytes // table.num_rows)
    return max(MIN_ROWS_PER_GROUP, ROW_GROUP_TARGET_BYTES // average)


def open_file(path):
    """Open an encrypted Parquet file for reading."""
    return pq.ParquetFile(path, decryption_properties=_decryption_properties())


def write_frames(path, frames):
    """Write frames to one encrypted file, a row group at a time.

    Returns the number of rows written. Nothing larger than a batch is held in
    memory, so the caller decides how much to read at once.
    """
    writer = None
    group_size = None
    written = 0
    try:
        for frame in frames:
            if frame is None or frame.empty:
                continue
            table = pa.Table.from_pandas(frame, preserve_index=False)
            if writer is None:
                group_size = rows_per_group(table)
                writer = pq.ParquetWriter(
                    path, table.schema, encryption_properties=_encryption_properties()
                )
            else:
                table = table.cast(writer.schema)
            writer.write_table(table, row_group_size=group_size)
            written += table.num_rows
    finally:
        if writer is not None:
            writer.close()
    return written


def _groups(paths):
    """Yield (path, row_group_index, num_rows, min_timestamp, max_timestamp)."""
    for path in paths:
        handle = open_file(path)
        metadata = handle.metadata
        names = [metadata.schema.column(i).name for i in range(metadata.num_columns)]
        column = names.index(TIMESTAMP_COLUMN) if TIMESTAMP_COLUMN in names else None
        for index in range(metadata.num_row_groups):
            group = metadata.row_group(index)
            low = high = None
            if column is not None:
                statistics = group.column(column).statistics
                if statistics is not None and statistics.has_min_max:
                    low, high = statistics.min, statistics.max
            yield path, index, group.num_rows, low, high


def _overlaps(low, high, start, end):
    """Whether a group's timestamp range can contain a matching row."""
    if low is None or high is None:
        return True
    if start is not None and high < pd.Timestamp(start):
        return False
    if end is not None and low > pd.Timestamp(end):
        return False
    return True


def _within(low, high, start, end):
    """Whether every row in the group matches, so it need not be read."""
    if low is None or high is None:
        return False
    if start is not None and low < pd.Timestamp(start):
        return False
    if end is not None and high > pd.Timestamp(end):
        return False
    return True


def _filtered(frame, start, end):
    if start is not None:
        frame = frame[frame[TIMESTAMP_COLUMN] >= pd.Timestamp(start)]
    if end is not None:
        frame = frame[frame[TIMESTAMP_COLUMN] <= pd.Timestamp(end)]
    return frame


def _read_group(path, index):
    return open_file(path).read_row_group(index).to_pandas()


def count_rows(paths, start_date=None, end_date=None):
    """Count matching rows, reading only groups the filter cuts through."""
    total = 0
    for path, index, num_rows, low, high in _groups(paths):
        if not _overlaps(low, high, start_date, end_date):
            continue
        if _within(low, high, start_date, end_date):
            total += num_rows
            continue
        total += len(_filtered(_read_group(path, index), start_date, end_date))
    return total


def read_rows(paths, limit=None, offset=0, start_date=None, end_date=None):
    """Return a page of matching rows as a DataFrame.

    Groups before the offset are skipped without being read whenever their
    recorded range settles how many of their rows match.
    """
    remaining_offset = max(0, int(offset or 0))
    collected = []
    gathered = 0
    for path, index, num_rows, low, high in _groups(paths):
        if limit is not None and gathered >= limit:
            break
        if not _overlaps(low, high, start_date, end_date):
            continue
        if _within(low, high, start_date, end_date) and remaining_offset >= num_rows:
            remaining_offset -= num_rows
            continue

        frame = _filtered(_read_group(path, index), start_date, end_date)
        if remaining_offset:
            if remaining_offset >= len(frame):
                remaining_offset -= len(frame)
                continue
            frame = frame.iloc[remaining_offset:]
            remaining_offset = 0
        if limit is not None:
            frame = frame.iloc[: limit - gathered]
        gathered += len(frame)
        collected.append(frame)

    if not collected:
        return pd.DataFrame()
    return pd.concat(collected, ignore_index=True)


def combine(paths, destination):
    """Merge a type's per-archive files into one, a row group at a time."""
    def groups():
        for path, index, _rows, _low, _high in _groups(paths):
            yield _read_group(path, index)

    return write_frames(destination, groups())
