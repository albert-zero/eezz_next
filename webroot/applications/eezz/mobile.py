"""
This module implements

    * :py:class:`eezz.mobile.TMobileDevices`: Database access to TUser

The database table TUser holds the mobile device information per user
"""

from dataclasses import dataclass
from database    import TDatabaseTable
from typing      import List


@dataclass(kw_only=True)
class TMobileDevices(TDatabaseTable):
    """ Manage mobile device data for auto-login and document-key management
    """
    column_names: List[str] = None    #: :meta private:

    def __post_init__(self):
        # Set the title and the columns before initializing the TDatabaseTable
        self.title              = 'TUser'
        self.column_names       = ['CAddr', 'CDevice', 'CSid', 'CUser', 'CVector', 'CKey']
        super().__post_init__()

        for x in self.column_descr:
            x.type  = 'text'
            x.alias = x.header[1:].lower()

        self.column_descr[0].options     = 'not null'
        self.column_descr[0].primary_key = True
        super().prepare_statements()
        super().db_create()
