# -*- coding: utf-8 -*-
"""
    EezzServer: 
    High speed application development and 
    high speed execution based on HTML5
    
    Copyright (C) 2015  Albert Zedlitz

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
import itertools
import json
import sys
import logging
import threading

from   bs4                  import Tag
from   dataclasses          import dataclass
from   pathlib              import Path
from   importlib            import import_module
from   lark                 import Lark, Transformer, Tree
from   lark.exceptions      import UnexpectedCharacters
from   typing               import Dict, Callable, Type
from   threading            import Thread
from   Crypto.PublicKey     import RSA
from   collections          import UserList

def singleton(a_class):
    """ Singleton decorator """
    instances = {}

    def get_instance(**kwargs):
        if a_class not in instances:
            instances[a_class] = a_class(**kwargs)
        return instances[a_class]
    return get_instance


@singleton
@dataclass(kw_only=True)
class TService:
    """ Container for environment """
    root_path:        Path = None        # WEB root path
    document_path:    Path = None        # EEZZ documents
    application_path: Path = None        # applications root path like applications/eezz
    public_path:      Path = None        # WEB public path for local HTML files
    resource_path:    Path = None        # Resources: lark-grammar, websocket.js, templates.html, ...
    locales_path:     Path = None        # translation files
    host:             str  = 'localhost'
    websocket_addr:   int  = 8100        # WEB socket address
    global_objects:   dict = None        # Objects assigned to HTML-tags
    translate:        bool = False       # Generate a translation template of set to True
    async_methods:    Dict[Callable, Thread] = None
    private_key:      RSA.RsaKey = None  # Global private key
    public_key:       RSA.RsaKey = None  # Global public key
    database_path:    Path = None        # sqlite database
    eezz_service_id:  str  = None        # Bluetooth EEZZ service ID

    def __post_init__(self):
        """ Generate RSA key pair and set environment variables """
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

    def assign_object(self, obj_id: str, description: str, attrs: dict, a_tag: Tag = None) -> None:
        """ Assigns an object to an HTML tag
        :exception IndexError: description systax does not match
        :exception AttributeError: Class not found
        :param obj_id:  Unique object-id
        :param description: Path to the class: <directory>.<module>.<class>
        :param attrs: Attributes for the constructor
        :param a_tag: Parent tag which handles an instance of this object
        :return:
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
        :exception AttributeError: Class has no method with the given name
        :param obj_id:
        :param a_method_name:
        :return: tuple(object, method, parent-tag)
        """
        try:
            x_object, x_tag, x_descr = self.global_objects[obj_id]
            x_method = getattr(x_object, a_method_name)
            return x_object, x_method, x_tag
        except AttributeError as x_except:
            logging.error(msg=f'assign failed: module {x}.{y} has mo class {z}')
            raise x_except

    def get_object(self, obj_id: str) -> UserList:
        """ Get the object for a given ID
        :param obj_id: Unique object ID
        :return: A TTable object
        """
        x_object, x_tag, x_descr = self.global_objects[obj_id]
        return x_object


class TServiceCompiler(Transformer):
    """ Transforms the parser tree into a list of dictionaries """
    def __init__(self, a_tag: Tag, a_id: str = '', a_query: dict = None):
        """ The transformer output is in json format
        :param a_tag:   The parent tag
        :param a_id:    The unique object id
        :param a_query: The URL query part
        """
        super().__init__()
        self.m_id       = a_id
        self.m_tag      = a_tag
        self.m_query    = a_query
        self.m_service  = TService()

        # Generator section for primitive statements
        self.simple_str       = lambda item: ''.join([str(x) for x in item])
        self.escaped_str      = lambda item: ''.join([x.strip('"') for x in item])
        self.qualified_string = lambda item: '.'.join([str(x) for x in item])
        self.list_updates     = lambda item: list(itertools.accumulate(item, lambda a, b: a | b))[-1]
        self.list_arguments   = lambda item: list(itertools.accumulate(item, lambda a, b: a | b))[-1]
        self.update_section   = lambda item: {'update':   item[0]}
        self.download         = lambda item: {'download': {'document': item[0].children[0], 'file': item[1].children[0]}}
        self.update_item      = lambda item: {item[0]: item[1]} if len(item) == 2 else {item[0]: item[0]}
        self.assignment       = lambda item: {item[0]: item[1]}
        self.format_string    = lambda item: f'{{{".".join(item)}}}'
        self.format_value     = lambda item: f'{{{".".join([str(x[0]) for x in item[0].children])}}}'

    def template_section(self, item):
        if item[0] in ('name', 'match', 'template'):
            self.m_tag[f'data-eezz-{item[0]}'] = item[1]
        return {item[0]: item[1]}

    def funct_assignment(self, item):
        x_function, x_args = item[0].children
        self.m_tag['onclick'] = 'eezzy_click(event, this)'
        return {'call': {'function': x_function, 'args': x_args, 'id': self.m_id}}

    def post_init(self, item):
        x_function, x_args = item[0].children
        x_json_obj = {'call': {'function': x_function, 'args': x_args, 'id': self.m_id}}
        self.m_tag['data-eezz-init'] = json.dumps(x_json_obj)
        print(self.m_tag)
        return x_json_obj

    def table_assignment(self, item):
        """ The table assignment uses TQuery to format arguments
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
    """ Data class to perform a format function. Attributes are provided dynamically """
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
    x_service  = TService(root_path=r'C:\Users\alzer\Projects\github\eezz_full\webroot')
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
    except UnexpectedCharacters as x_except:
        logger.error(msg='Test parser exception successful', stack_info=True)



