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
    """
    TMobileDevices class handles the configuration and initialization of a mobile devices table in a database.
    It sets up the necessary table columns, types, and primary keys before finalizing the table creation.

    The class is primarily used to manage mobile device records and their associated information by inheriting
    from TDatabaseTable and setting additional parameters specific to mobile devices.

    :ivar column_names: List of column names for the database table.
    :type column_names: List[str]
    """
    column_names: List[str] = None    #: :meta private:

    def __post_init__(self):
        """
        TUser class manages the setup and initialization of user-related database
        table configurations including column names, types, and primary key.

        :ivar title:        The title of the table.
        :ivar column_names: The list of column names in the table.
        :ivar column_descr: A list of column descriptions containing metadata for
                            each column.

        :return: None
        """
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
        super().create_database()
