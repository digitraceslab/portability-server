portability-server
==================

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   apimodules



Data Format
------------

Downloaded data is stored in the `data/` directory. The initial data format depends
on the source. For example, Google Portability exports are ZIP files containing data
files in multiple formats. All stored data is encrypted, includign these initial
files.

The celery service run data processing periodically. During processing data files are
extracted and processed data is stored in csv format. Each requested data type is
stored in a separate csv file and may contain multiple columns with different data types.
Each row contains a timestamp column, which is converted to unix time in seconds.






