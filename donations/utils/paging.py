"""Lazy row source for Django's Paginator over a donation's stored data.

The paginator only needs a length and slicing, so a page costs one count and
one read of exactly the rows it shows, rather than loading thousands of rows
to display fifty.
"""


class DonationRows:
    """Sequence view of one data type's rows, read on demand.

    ``len()`` comes from the donation's row count; a slice is fetched with the
    slice's own offset and length. Only slices are supported, which is all
    ``Paginator`` uses.
    """

    def __init__(self, donation, data_type, start_date=None, end_date=None):
        self._donation = donation
        self._data_type = data_type
        self._start_date = start_date
        self._end_date = end_date
        self._count = None

    def __len__(self):
        if self._count is None:
            self._count = self._donation.count_rows(
                self._data_type, start_date=self._start_date, end_date=self._end_date
            )
        return self._count

    def __getitem__(self, item):
        if not isinstance(item, slice):
            raise TypeError("DonationRows supports slicing only")
        start = item.start or 0
        stop = len(self) if item.stop is None else item.stop
        if stop <= start:
            return []
        return self._donation.fetch_data(
            self._data_type,
            limit=stop - start,
            offset=start,
            start_date=self._start_date,
            end_date=self._end_date,
        )
