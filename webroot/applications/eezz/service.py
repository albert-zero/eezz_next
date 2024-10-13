# -*- coding: utf-8 -*-
"""
    This module implements the following classes:

    * **TGlobalService**: Container for global environment
    * **TService**: A singleton for TGlobalService
    * **TServiceCompiler**: A Lark compiler for HTML EEZZ extensions
    * **TTranslate**: Extract translation info from HTML to create a POT file
    * **TQuery**: Class representing the query of an HTML request

"""
import itertools
import json
import sys
import logging

from   bs4                  import Tag
from   dataclasses          import dataclass
from   pathlib              import Path
from   importlib            import import_module
from   lark                 import Lark, Transformer, Tree
from   lark.exceptions      import UnexpectedCharacters
from   typing               import Dict, Callable, Any, TypeVar, ClassVar
from   threading            import Thread
from   Crypto.PublicKey     import RSA

T = TypeVar('T')


class TGlobal:
    instances: dict = {}

    @classmethod
    def get_instance(cls, cls_type):
        if not cls.instances.get(cls_type):
            cls.instances[cls_type] = cls_type()
        return cls.instances[cls_type]


@dataclass(kw_only=True)
class TService:
    """ Container for global environment
    """
    root_path:        Path = None
    """ Root path for the HTTP server """
    document_path:    Path = None
    """ Path to EEZZ documents  """
    application_path: Path = None
    """ Path to applications using the browser interface  """
    public_path:      Path = None
    resource_path:    Path = None
    locales_path:     Path = None
    host:             str  = 'localhost'
    websocket_addr:   int  = 8100
    global_objects:   dict = None
    translate:        bool = False
    async_methods:    Dict[Callable, Thread] = None
    private_key:      RSA.RsaKey = None
    public_key:       RSA.RsaKey = None
    database_path:    Path = None
    eezz_service_id:  str  = None
    singletons:       ClassVar[dict] = dict()

    def __post_init__(self):
        """ Generate RSA key pair and set environment variables
        """
        x_mod  = int("C5F23FA172317A1F6930C0F9AF79FF044D34BFD1336E5A174155953487A4FF0C744A093CA7044F39842AC685AB37C55F1F01F0055561BAD9C3EEA22B28D09F061875ED5BDB2F1F2B797B1BEF6534C0D4FCEFAFFA8F3A91396961165241564BD6E3CA08023F2A760A0B54A4A6A996CDF7DE3491468C199566EE5993FCFD03A2B285AD6FBBC014A20C801618EE19F88EB8E6359624A35FDD7976F316D6AB225CF85DA5E63AB30248D38297A835CF16B9799973C2F9F05F5F850B3152B3A05F06FEC0FBDA95C70911F59F6A11A1451822ABFE4FE5A021F7EA983BDE9F442302891DCF51B7322EAFB88950F2617B7120F9B87534719DCA27E87D82A183CB37BC7045", 16)
        x_exp  = int("10001", 16)
        self.private_key = RSA.construct((x_mod, x_exp))
        self.public_key  = self.private_key.public_key()

        if not self.root_path:
            self.root_path = Path(r'C:\Users\alzer\Projects\github\eezz_full\webroot')
        if isinstance(self.root_path, str):
            self.root_path = Path(self.root_path)

        self.resource_path    = self.root_path      / 'resources'
        self.public_path      = self.root_path      / 'public'
        self.application_path = self.root_path      / 'applications'
        self.document_path    = self.root_path      / 'database'
        self.database_path    = self.document_path  / 'eezz.db'
        self.locales_path     = self.resource_path  / 'locales'
        self.logging_path     = self.root_path      / 'logs'
        self.global_objects   = dict()
        self.logging_path.mkdir(exist_ok=True)

    @classmethod
    def set_instance(cls, instance):
        cls.singletons[type(instance)] = instance

    @classmethod
    def get_instance(cls, class_type = None):
        if not class_type:
            class_type = cls
        return cls.singletons.get(class_type)

    def assign_object(self, obj_id: str, description: str, attrs: dict, a_tag: Tag = None) -> None:
        """ _`assign_object` Assigns an object to an HTML tag

        :raise IndexError: description systax does not match
        :raise AttributeError: Class not found
        :param obj_id:  Unique object-id
        :param description: Path to the class: <directory>.<module>.<class>
        :param attrs: Attributes for the constructor
        :param a_tag: Parent tag which handles an instance of this object
        """
        try:
            x_list  = description.split('.')
            x, y, z = x_list[0], x_list[1], x_list[2]
        except IndexError as x_except:
            logging.error(msg=f'assign_object: description has to match: <directory>.<module>.<class>: {description}')
            raise x_except

        x_path = self.application_path / x

        if not str(x_path) in sys.path:
            sys.path.append(str(x_path))

        try:
            x_module    = import_module(y)
            x_class     = getattr(x_module, z)
            x_object    = x_class(**attrs)
            self.global_objects.update({obj_id: (x_object, a_tag, description)})
        except AttributeError as x_except:
            logging.error(msg=f'assign_object: module {x}.{y} has mo class {z}')
            raise x_except

    def get_method(self, obj_id: str, a_method_name: str) -> tuple:
        """ Get a method by name for a given object

        :raise AttributeError: Class has no method with the given name
        :param obj_id: Unique hash-ID for object as stored in :py:meth:`eezz.service.TService.assign_object`
        :param a_method_name:
        :return: tuple(object, method, parent-tag)
        """
        try:
            x_object, x_tag, x_descr = self.global_objects[obj_id]
            x_method = getattr(x_object, a_method_name)
            return x_object, x_method, x_tag
        except AttributeError as x_except:
            raise x_except

    def get_object(self, obj_id: str) -> Any:
        """ Get the object for a given ID

        :param obj_id: Unique hash-ID for object as stored in :func:`eezz.service.TGlobalService.assign_object`
        :return: The assigned object
        """
        x_object, x_tag, x_descr = self.global_objects[obj_id]
        return x_object


class TServiceCompiler(Transformer):
    """ Transforms the parser tree into a list of dictionaries
    The transformer output is in json format

    :param a_tag:   The parent tag
    :type a_tag:    BeautifulSoup4.Tag
    :param a_id:    A unique object id
    :param a_query: The URL query part
    """
    def __init__(self, a_tag: Tag, a_id: str = '', a_query: dict = None):
        super().__init__()
        self.m_id       = a_id
        self.m_tag      = a_tag
        self.m_query    = a_query
        self.m_service  = TService()

    @staticmethod
    def simple_str(item):
        """ Parse a string token """
        return ''.join([str(x) for x in item])

    @staticmethod
    def escaped_str(item):
        """ Parse an escaped string """
        return ''.join([x.strip('"') for x in item])

    @staticmethod
    def qualified_string(item):
        """ Parse a qualified string: ``part1.part2.part3`` """
        return '.'.join([str(x) for x in item])

    @staticmethod
    def list_updates(item):
        """ Accumulate 'update' statements """
        return list(itertools.accumulate(item, lambda a, b: a | b))[-1]

    @staticmethod
    def list_arguments(item):
        """ Accumulate arguments for function call """
        return list(itertools.accumulate(item, lambda a, b: a | b))[-1]

    @staticmethod
    def update_section(item):
        """ Parse 'update' section """
        return {'update':   item[0]}

    @staticmethod
    def download(item):
        """ Parse 'download' section """
        return {'download': {'document': item[0].children[0], 'file': item[1].children[0]}}

    @staticmethod
    def update_item(item):
        """ Parse 'update' expression"""
        return {item[0]: item[1]} if len(item) == 2 else {item[0]: item[0]}

    @staticmethod
    def assignment(item):
        """ Parse 'assignment' expression: ``variable = value`` """
        return {item[0]: item[1]}

    @staticmethod
    def format_string(item):
        """ Create a format string: ``{value}`` """
        return f'{{{".".join(item)}}}'

    @staticmethod
    def format_value(item):
        """ Create a format string: ``{key.value}`` """
        return  f'{{{".".join([str(x[0]) for x in item[0].children])}}}'

    def template_section(self, item):
        """ Create tag attributes """
        if item[0] in ('name', 'match', 'template'):
            self.m_tag[f'data-eezz-{item[0]}'] = item[1]
        return {item[0]: item[1]}

    def funct_assignment(self, item):
        """ Parse 'function' section """
        x_function, x_args = item[0].children
        self.m_tag['onclick'] = 'eezzy_click(event, this)'
        return {'call': {'function': x_function, 'args': x_args, 'id': self.m_id}}

    def post_init(self, item):
        """ Parse 'post-init' section for function assignment """
        x_function, x_args = item[0].children
        x_json_obj = {'call': {'function': x_function, 'args': x_args, 'id': self.m_id}}
        self.m_tag['data-eezz-init'] = json.dumps(x_json_obj)
        return x_json_obj

    def table_assignment(self, item):
        """ Parse 'assign' section, assigning a Python object to an HTML-Tag
        The table assignment uses TQuery to format arguments
        In case the arguments are not all present, the format is broken and process continues with default """
        x_function, x_args = item[0].children

        try:
            x_query = TQuery(self.m_query)
            x_args  = {x_key: x_val.format(query=x_query) for x_key, x_val in x_args.items()}
        except AttributeError as x_except:
            logging.debug(msg=f'table_assignment: {x_function}, {x_args}')
        self.m_service.assign_object(self.m_id, x_function, x_args, self.m_tag)
        return {'assign': {'function': x_function, 'args': x_args, 'id': self.m_id}}


class TTranslate:
    @staticmethod
    def generate_pot(a_soup, a_title):
        """ Generate a POT file from HTML file

        :param a_soup: The HTML page for translation
        :param a_title: The file name for the POT file
        """
        try:
            x_pot_file = TService().locales_path / f'{a_title}.pot'
            x_elements = a_soup.find_all(lambda x_tag: x_tag.has_attr('data-eezz-i18n'))
            x_path_hdr = TService().locales_path / 'template.pot'
            with x_pot_file.open('w', encoding='utf-8') as f:
                with x_path_hdr.open('r', encoding='utf-8') as f_hdr:
                    f.write(f_hdr.read())
                for x_elem in x_elements:
                    f.write(f"msgid  \"{x_elem['data-eezz-i18n']}\"\n"
                            f"msgstr \"{[str(x) for x in x_elem.descendants]}\"\n\n")
        except FileNotFoundError as x_except:
            logging.error(msg=f'Creation of POT file is not possible', stack_info=True, stacklevel=3)


@dataclass(kw_only=True)
class TQuery:
    """ Transfer the HTTP query to class attributes

    :param query: The query string in dictionary format
    """
    def __init__(self, query: dict):
        if query:
            for x_key, x_val in query.items():
                setattr(self, x_key, ','.join(x_val))


# --- Section for module tests
def test_parser(source: str):
    x_parent_tag   = Tag(name='text')
    x_parser       = Lark.open(str(Path(TService().resource_path) / 'eezz.lark'))

    try:
        x_syntax_tree  = x_parser.parse(source)
        x_transformer  = TServiceCompiler(x_parent_tag, 'Directory')
        x_list_json    = x_transformer.transform(x_syntax_tree)
        if type(x_list_json) is Tree:
            logging.debug(msg=list(itertools.accumulate(x_list_json.children, lambda a, b: a | b))[-1] )
        else:
            logging.debug(msg=x_list_json)
    except UnexpectedCharacters as x_ex:
        logging.error(msg=f'invalid expression: parent-tag={x_parent_tag.name}, position={x_ex.pos_in_stream}', stack_info=True, stacklevel=3)
        raise x_ex


if __name__ == '__main__':
    x_service  = TService(root_path=Path(r'C:\Users\alzer\Projects\github\eezz_full\webroot'))
    TService.set_instance(x_service)

    x_log_path = x_service.logging_path / 'app.log'
    logging.basicConfig(filename=x_log_path, filemode='w', style='{', format='{name} - {levelname} - {message}')
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # test parser
    logger.debug(msg="application.eezz.service.__main__:")
    logger.debug(msg="Test the parser: assign statement")
    test_parser(source="""assign:   examples.directory.TDirView(path=".")""")

    logger.debug(msg="Test the parser: download statement")
    test_parser(source="""download: document(name=test1, author=albert), files( main=main, prev=prev )""")

    # test parser exception and logging
    logger.debug(msg="Test the parser: wrong download statement:")
    logger.debug(msg="download: files(name=test1, author=albert), documents( main=main, prev=prev )")

    try:
        test_parser(source="""download: files(name=test1, author=albert), documents( main=main, prev=prev )""")
    except UnexpectedCharacters as xx_except:
        logger.error(msg='Test parser exception successful', stack_info=True)



