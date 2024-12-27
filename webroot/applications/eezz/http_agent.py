# -*- coding: utf-8 -*-
"""
    * **THttpAgent**: Handle WEB-Socket requests

    The interaction with the JavaScript via WEB-Socket includes generation of HTML parts for user interface updates
"""
import io
import json
import uuid
import copy
import re
from copy import deepcopy

from pathlib   import Path
from typing    import Callable
from bs4       import Tag, BeautifulSoup, NavigableString
from itertools import product, chain
from table     import TTable, TTableCell, TTableRow
from websocket import TWebSocketAgent
from service   import TService, TServiceCompiler, TTranslate
from lark      import Lark, UnexpectedCharacters, Tree, UnexpectedEOF
from loguru    import logger

class THttpAgent(TWebSocketAgent):
    """ Agent handles WEB socket events """

    def __init__(self):
        super().__init__()
        self.soup = None

    @logger.catch
    def handle_request(self, request_data: dict) -> dict | None:
        """ Handle WEB socket requests

        * **initialize**: The browser sends the complete HTML for analysis.
        * **call**: The request issues a method call and the result is sent back to the browser

        :param request_data: The request send by the browser
        :return: Response in JSON stream, containing valid HTML parts for the browser
        """
        x_updates = list()
        x_tasks   = list()
        x_result  = dict()
        x_id      = ''

        if 'initialize' in request_data:
            self.soup   = BeautifulSoup(request_data['initialize'], 'html.parser', multi_valued_attributes=None)
            for x in self.soup.css.select('table[data-eezz-compiled]'):
                x_html = self.generate_html_table(x, x['id'])
                x_id   = x['id']

                if x.attrs.get('data-eezz-json'):
                    x_gen_request = {'call': {'function': 'get_header_row', 'args': {}, 'id': x_id}}
                    x_gen_request.update(json.loads(x['data-eezz-json']))
                    x_tasks.append((x_id, x_gen_request))

                x_updates.append({'target': f'{x_id}.caption.innerHTML', 'value': x_html['caption']})
                x_updates.append({'target': f'{x_id}.thead.innerHTML',   'value': x_html['thead']})
                x_updates.append({'target': f'{x_id}.tbody.innerHTML',   'value': x_html['tbody']})

            for x in  self.soup.css.select('select[data-eezz-compiled], .clzz_grid[data-eezz-compiled]'):
                x_html = self.generate_html_grid(x)
                x_id   = x['id']
                x_updates.append({'target': f'{x_id}.innerHTML', 'value': x_html['tbody']})

                if x.attrs.get('data-eezz-json'):
                    x_gen_request = {'call': {'function': 'get_header_row', 'args': {}, 'id': x_id}}
                    x_gen_request.update(json.loads(x['data-eezz-json']))
                    x_tasks.append((x_id, x_gen_request))

            # manage translation if service started with command line option --translate:
            if TService().translate:
                x_translate = TTranslate()
                x_translate.generate_pot(self.soup, request_data['title'])
            x_result.update({'update': x_updates, 'event': 'init', 'tasks': x_tasks})
            return x_result

        # A call request consists of a method to call and a set ot tags to update (updates might be an empty list)
        # The update is a list of key-value pairs separated by a colon
        # The key is the html-element-id and the attribute separated by dot
        # The value is a valid HTML string to replace either the attribute value or part of the element (for example table.body)
        if 'call' in request_data and 'update' in request_data:
            try:
                # Only interested in the x_tag object, which is stored as key(object, method-name) in the TService database
                # The method itself is executed in module TWebSocketClient
                x_event     = request_data['call']
                x_event_id  = x_event['id']
                x_row       = request_data['result']

                x_obj, x_method, x_tag, x_descr  = TService().get_method(x_event_id, x_event['function'])
                for x_key, x_value in request_data['update'].items():
                    if x_key == 'this.tbody':
                        x_html = self.generate_html_table(x_tag, x_tag['id'])
                        x_updates.append({'target': f'{x_tag["id"]}.tbody.innerHTML', 'value': x_html['tbody']})
                    elif x_key == 'this.subtree_view':
                        tag_tree_template = self.soup.css.select(f'#{x_value}')[0]
                        tag_row_template  = tag_tree_template.soup.css.select('[data-eezz-template=row]')[0]
                        x_subtree_view    = self.generate_html_grid_item(tag_row_template, x_row)
                        x_updates.append({'target': f'{x_value}.innerHTML', 'value': str(x_subtree_view)})
                    elif x_key == 'this.subtree':
                        x_id            = x_row.id
                        x_table: TTable = x_row.child

                        if x_table is None:
                            # There is no subtree: Send empty value to collapse the tree view
                            x_updates.append({'target': f'{x_id}.subtree', 'value': ''})
                        else:
                            # Send a subtree template and data
                            TService().objects.update({x_id: (x_table, x_tag, x_descr)})
                            x_tag_list = x_tag.css.select('tr[data-eezz-json]')

                            for x in x_tag_list:
                                if x.get('call'):
                                    x['call']['id'] = x_id

                            x_html = self.generate_html_table(x_tag, x_id)
                            x_id   = x_row.id
                            x_updates.append({'target': f'{x_id}.subtree', 'value': {'option': x_value, 'template': x_html['template'], 'thead': x_html['thead'], 'tbody': x_html['tbody']}})
                    else:
                        for x in request_data['result-value']:
                            x_updates.append(x)

            except KeyError as ex:
                logger.warning(f'KeyError {ex.args}')

            x_result = {'update': x_updates}
            return x_result

    def do_get(self, a_resource: Path | str, a_query: dict) -> str:
        """ Response to an HTML GET command

        The agent reads the source, compiles the data-eezz sections and adds the web-socket component
        It returns the enriched document

        :param a_resource: The path to the HTML document, containing EEZZ extensions
        :type a_resource: pathlib.Path
        :param a_query: The query string of the URL
        :return: The compiled version of the HTML file
        """
        x_html    = a_resource
        x_service = TService()
        if isinstance(a_resource, Path):
            with a_resource.open('r', encoding="utf-8") as f:
                x_html = f.read()

        x_parser     = Lark.open(str(Path(x_service.resource_path) / 'eezz.lark'))
        x_soup       = BeautifulSoup(x_html, 'html.parser', multi_valued_attributes=None)

        # The template table is used to add missing structures as default
        x_templ_path = x_service.resource_path / 'template.html'
        with x_templ_path.open('r') as f:
            x_template = BeautifulSoup(f.read(), 'html.parser', multi_valued_attributes=None)

        x_templ_table = x_template.body.table
        for x_chrom in x_soup.css.select('table[data-eezz]'):
            if not x_chrom.css.select('caption'):
                x_chrom.append(copy.deepcopy(x_templ_table.caption))
            if not x_chrom.css.select('thead'):
                x_chrom.append(copy.deepcopy(x_templ_table.thead))
            if not x_chrom.css.select('tbody'):
                x_chrom.append(copy.deepcopy(x_templ_table.tbody))
            if not x_chrom.css.select('tfoot'):
                # x_chrom.append(copy.deepcopy(x_temp_table.tfoot))
                pass
            if not x_chrom.has_attr('id'):
                x_chrom['id'] = str(uuid.uuid1())[:8]
            # Compile subtree using the current table id for events
            self.compile_data(x_parser, x_chrom.css.select('[data-eezz]'), x_chrom['id'])

        for x_chrom in x_soup.css.select('select[data-eezz], .clzz_grid[data-eezz]'):
            if not x_chrom.has_attr('id'):
                x_chrom['id'] = str(uuid.uuid1())[:8]
            self.compile_data(x_parser, x_chrom.css.select('[data-eezz]'), x_chrom['id'])

        # Compiling the reset of the document
        self.compile_data(x_parser, x_soup.css.select('[data-eezz]'), '', a_query)
        return x_soup.prettify()

    @staticmethod
    def compile_data(a_parser: Lark, a_tag_list: list, a_id: str, a_query: dict = None, recompile: bool = False) -> None:
        """ Compile data-eezz-json to data-eezz-compile,
        create tag attributes and generate tag-id to manage incoming requests

        :param recompile:
        :param a_parser: The Lark parser to compile EEZZ to json
        :param a_tag_list: HTML-Tag to compile
        :param a_id: The ID of the tag to be identified for update
        :param a_query: The query of the HTML request
        :return: None
        """
        # logger.debug(f'compile data \n{a_tag_list[0].prettify()}')
        x_service = TService()
        for x_tag in a_tag_list:
            x_id   = a_id
            # --- get instead of pop
            x_data = x_tag.attrs.get('data-eezz')
            try:
                if not x_data:
                    return
                if not x_id:
                    if x_tag.has_attr('id'):
                        x_id = x_tag.attrs['id']

                if x_tag.get('data-eezz-compiled') == "ok" and not recompile:
                    continue

                x_syntax_tree = a_parser.parse(x_data)
                x_transformer = TServiceCompiler(x_tag, x_id, a_query)
                x_tree        = x_transformer.transform(x_syntax_tree)
                x_json        = dict()
                x_list_items  = x_tree.children if isinstance(x_tree, Tree) else [x_tree]
                x_tag['data-eezz-compiled'] = "ok"

                for x_part in x_list_items:
                    x_part_json = {x_part_key: x_part_val for x_part_key, x_part_val in x_part.items() if x_part_key in ('update', 'call')}
                    x_json.update(x_part_json)

                if x_json:
                    x_tag['data-eezz-json'] = json.dumps(x_json)

                # logger.debug(f'{x_data} ==> {x_list_items}')
                if x_tag.has_attr('data-eezz-template') and x_tag['data-eezz-template'] == 'websocket':
                    x_path      = Path(x_service.resource_path / 'websocket.js')
                    x_ws_descr  = """var g_eezz_socket_addr = "ws://{host}:{port}";\n """.format(host=TService().host, port=TService().websocket_addr, args='')
                    x_ws_descr += """var g_eezz_arguments   = "{args}";\n """.format(args='')
                    with x_path.open('r') as f:
                        x_ws_descr += f.read()
                    x_tag.string = x_ws_descr
            except (UnexpectedCharacters,  UnexpectedEOF) as ex:
                logger.error(f'{repr(ex)}  position = {ex.pos_in_stream} in {x_data=}')
                logger.exception(ex)

    @staticmethod
    def format_attributes(a_key: str, a_value: str, a_fmt_funct: Callable) -> str:
        """ Eval template tag-attributes, diving deep into data-eezz-json

        :param a_key:       Thw key string to pick the items in an HTML tag
        :param a_value:     The dictionary in string format to be formatted
        :param a_fmt_funct: The function to be called to format the values
        :return: The formatted string
        """
        if a_key == 'data-eezz-json':
            x_json = json.loads(a_value)
            if 'call' in x_json:
                x_args = x_json['call']['args']
                x_json['call']['args'] = {x_key: a_fmt_funct(x_val) for x_key, x_val in x_args.items()}
            x_fmt_val = json.dumps(x_json)
        else:
            x_fmt_val = a_fmt_funct(a_value)
        return x_fmt_val

    def generate_html_cells(self, a_tag: Tag, a_cell: TTableCell) -> Tag:
        """ Generate HTML cells
        Input for the lamda is a string and output is formatted according to the TTableCell object

        :param a_tag:   The parent tag to generate the table cells
        :param a_cell:  The template cell to format to HTML
        :return: The formatted HTML tag
        """
        x_fmt_attrs = {x: self.format_attributes(x, y, lambda z: z.format(cell=a_cell)) for x, y in a_tag.attrs.items()}
        x_new_tag   = copy.deepcopy(a_tag)
        for x in x_new_tag.descendants:
            if x and isinstance(x, Tag):
                x.string = x.string.format(cell=a_cell)
        if x_new_tag.string:
            x_new_tag.string = a_tag.string.format(cell=a_cell)
        x_new_tag.attrs  = x_fmt_attrs

        # store the date-time in attribute, so it could be used for in-place formatting:
        if a_cell.type in ('datetime', 'date', 'time'):
            x_new_tag['timestamp'] = str(a_cell.value.timestamp())
        return x_new_tag

    def generate_html_rows(self, a_html_cells: list, a_tag: Tag, a_row: TTableRow) -> Tag:
        """ This operation add fixed cells to the table.
        Cells which are not included as template for table data are used to add a constant info to the row

        :param a_html_cells: A list of cells to build up a row
        :param a_tag: The parent containing the templates for the row
        :param a_row: The table row values to insert
        :return: The row with values rendered to HZML
        """
        x_fmt_attrs  = {x: self.format_attributes(x, y, lambda z: z.format(row=a_row)) for x, y in a_tag.attrs.items()}
        # x_html_cells = a_html_cells
        x_html_cells = [[copy.deepcopy(x)] if not x.has_attr('data-eezz-compiled') else a_html_cells for x in a_tag.css.select('th,td')]
        x_html_cells = list(chain.from_iterable(x_html_cells))
        try:
            for x in x_html_cells:
                if x.has_attr('reference') and x['reference'] == 'row':
                    for x_child in x.descendends:
                        if isinstance(x_child, NavigableString):
                            x.parent.string = x.format(row=a_row)
        except AttributeError as ex:
            logger.debug(str(ex))

        if a_row.row_id:
            x_fmt_attrs['id'] = a_row.id

        x_new_tag = Tag(name=a_tag.name, attrs=x_fmt_attrs)
        for x in x_html_cells:
            x_new_tag.append(x)
        return x_new_tag

    def generate_html_table(self, a_table_tag: Tag, a_id: str) -> dict:
        """ Generates a table structure in four steps

        1. Get the column order and the viewport
        2. Get the row templates
        3. Evaluate the table cells
        4. Send the result separated by table main elements

        :param a_id:
        :param a_table_tag: The parent table tag to produce the output
        """
        x_table_obj: TTable = TService().get_object(a_id)
        x_row_template = a_table_tag.css.select('tr[data-eezz-compiled]')
        x_row_viewport = list(x_table_obj.get_visible_rows())
        x_table_header = x_table_obj.get_header_row()

        # insert the header, so that we could manage header and body in a single stack
        x_row_viewport.insert(0, x_table_header)

        # Re-arrange the cells for output
        x_range           = list(range(len(x_table_header.cells)))
        x_range_cells     = [[x_row.cells[index] for index in x_range] for x_row in x_row_viewport]
        for x_row, x_cells in zip(x_row_viewport, x_range_cells):
            x_row.cells = x_cells

        # Evaluate match: It's possible to have a template for each row type (header and body):
        x_format_row      = [([x_tag for x_tag in x_row_template if x_tag.has_attr('data-eezz-match') and x_tag['data-eezz-match'] in x_row.type], x_row) for x_row in x_row_viewport]
        x_format_cell     = [(list(product(x_tag[0].css.select('td[data-eezz-compiled],th[data-eezz-compiled]'), x_row.cells)), x_tag[0], x_row) for x_tag, x_row in x_format_row if x_tag]

        # Put all together and create HTML
        x_list_html_cells = [([self.generate_html_cells(x_tag, x_cell) for x_tag, x_cell in x_row_templates], x_tag_tr, x_row) for x_row_templates, x_tag_tr, x_row in x_format_cell]
        x_list_html_rows  = [(self.generate_html_rows(x_html_cells, x_tag_tr, x_row)) for x_html_cells, x_tag_tr, x_row in x_list_html_cells]

        # separate header and body
        # x_tbl_id = a_table_tag["id"]
        x_html = dict()
        x_html['template'] = a_table_tag.prettify()
        x_html['caption']  = a_table_tag.caption.string.format(table=x_table_obj)
        x_html['thead']    = ''.join([str(x) for x in x_list_html_rows[:1]]) if len(x_list_html_rows) > 0 else ''
        x_html['tbody']    = ''.join([str(x) for x in x_list_html_rows[1:]]) if len(x_list_html_rows) > 1 else ''
        return x_html

    def generate_html_grid(self, a_tag: Tag) -> dict:
        """ Besides the table, supported display is grid (via class clzz_grid or select """
        x_row_template  = a_tag.css.select('[data-eezz-template=row]')
        x_table         = TService().get_object(a_tag.attrs['id'])
        x_row_viewport  = list(x_table.get_visible_rows())
        x_format_row    = [([x_tag for x_tag in x_row_template if x_tag.has_attr('data-eezz-match') and x_tag['data-eezz-match'] in x_row.type], x_row) for x_row in x_row_viewport]

        x_list_children = [self.generate_html_grid_item(x_tag[0], x_row) for x_tag, x_row in x_format_row]
        return {"tbody": ''.join([str(x) for x in x_list_children])}

    def generate_html_grid_item(self, tag_template: Tag, a_row: TTableRow) -> Tag:
        """ Generates elements of the same kind, derived from a template and update content
        according the row values

        :param tag_template: Template
        :param a_row:        Row with data to parse
        :param a_header:     Row meta information
        :return:             Generated HTML tag
        """
        # Generate name-value pairs for this row:
        x_format_cell    = {x: y for x, y in zip(a_row.column_descr, a_row.cells)}
        x_new_element    = deepcopy(tag_template)

        if x_cell_templates := x_new_element.css.select('[data-eezz-template=cell]'):
            for x_tag in x_cell_templates:
                x_ref   = x_tag.attrs.get('data-eezz-reference')
                x_cell  = x_format_cell.get(x_ref, None)
                x_attrs = {x_key: x_val.format(cell=x_cell) for x_key, x_val in x_tag.attrs.items() if not x_key.startswith('data-eezz')}

                for x_key, x_val in x_attrs.items():
                    x_tag.attrs.update({x_key: x_val})
                x_tag.attrs['data-eezz-index'] = str(x_cell.index)
                x_tag.attrs['id'] = f'{a_row.row_id}.{x_cell.index}'
                if x_tag.string:
                    x_tag.string = x_tag.string.format(cell=x_cell)

        if x_cell_templates := x_new_element.css.select('[data-eezz-template=detail]'):
            for x_tag in x_cell_templates:
                x_ref        = x_tag.attrs.get('data-eezz-reference')
                x_cell       = x_format_cell.get(x_ref, None)
                x_details    = x_cell.details
                x_detail_tag = x_tag

                for j, x_detail in enumerate(x_details):
                    if j > 0:
                        x_new_tag    = deepcopy(x_tag)
                        x_detail_tag.insert_after(x_new_tag)
                        x_detail_tag = x_new_tag

                    x_attrs = {x_key: x_val.format(detail=x_detail) for x_key, x_val in x_tag.attrs.items() if isinstance(x_val, str)}
                    for x_key, x_val in x_attrs.items():
                        x_detail_tag.attrs.update({x_key: x_val})
                    x_detail_tag.attrs['data-eezz-index'] = f'{str(x_cell.index)}.{j}'
                    x_tag.attrs['id'] = f'{a_row.row_id}.{x_cell.index}.{j}'
                    if x_detail_tag.string:
                        x_tag.string = x_tag.string.format(cell=x_cell)

        return x_new_element


if __name__ == '__main__':
    """:meta private:"""
    text2 = """
    <table id='directory' data-eezz='name: directory, assign: examples.directory.TDirView(path=".", title="dir")'>
        <tbody>
            <tr data-eezz='template: row, match: body,
                event  : expand_collapse(rowid={row.row_id}),
                update : this.subtree=restricted, path_label.innerHTML={row.row_id}'>
            </tr>
        </tbody>
    </table>
        """
    """
    
    """

    # list_table = aSoup.css.select('table[data-eezz]')
    TService.set_environment(root_path=r'C:\Users\alzer\Projects\github\eezz_full\webroot')
    xx_gen    = THttpAgent()
    xx_html   = xx_gen.do_get(text2, dict())
    xx_soup   = BeautifulSoup(xx_html, 'html.parser', multi_valued_attributes=None)

    # xx_h1_set = xx_soup.css.select('h1')
    # xx_h1     = xx_h1_set[0]
    # xx_str    = xx_h1.string
    # print(xx_str.parent)

    list_table = xx_soup.css.select('table')
    for xx in list_table:
        xjsn = xx['data-eezz']
        # json.loads( f'{{ {xjsn} }}' )
        # xjsn['assign'] = 'new values for subtree'
        logger.debug(f'{{ {xjsn} }}')

        xx_table  = xx_gen.generate_html_table(xx, xx['id'])
        debug_out = io.StringIO()
        print(json.dumps(xx_table, indent=2), file=debug_out)
        logger.debug(debug_out.getvalue())

    logger.success('done')
