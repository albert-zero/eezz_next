/**
 *  Copyright (C) 2015  Albert Zedlitz
 *  
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 *  
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *  
 *  You should have received a copy of the GNU General Public License
 *  along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

/* Variable declaration generated by agent:
 */
// var g_eezz_socket_addr = "ws://{host}:{port}";
// var g_eezz_arguments   = "{args}";
var g_eezz_web_socket;
window.onload = eezz_connect();

// User Callback Interface
class TEezz {
    constructor() {
        this.on_update  = (a_element) => {};
        this.on_animate = (a_element) => {};
    }
}

// Global user interface instance
eezz = new TEezz();

// Open and controlling the WEB socket
function eezz_connect() {
    console.log('connect websocket ...');
	g_eezz_web_socket        = new WebSocket(g_eezz_socket_addr);
    g_eezz_web_socket.onopen = function() {
        console.log('on open websocket...');
        var x_title   = "document";
        var x_title_tags = document.getElementsByTagName('title');
        if (x_title_tags.length > 0) {
            x_title   = x_title_tags[0].innerHTML;
        }
        var x_body    = document.body;
        var x_json    = {"initialize": x_body.innerHTML, "args": g_eezz_arguments, 'title': x_title};
        g_eezz_web_socket.send(JSON.stringify(x_json));
    }
    
    /* Error handling: Reopen connection */
    g_eezz_web_socket.onerror = function(a_error) {
        console.log('error on websocket ...');
        window.console.error(a_error);
    }

    /* Wait for the application and update the document          */
    g_eezz_web_socket.onmessage = function(a_event) {
        var x_json = JSON.parse(a_event.data)

        // The main response is an update request
        if (x_json.update) {
            console.log('update  ');
            var x_array_descr = x_json.update;
            var x_element_map = new Map();

            for (var i = 0; i < x_array_descr.length; i++) {
                console.log("update " + x_array_descr[i]);
                var x_update_json = x_array_descr[i];

                try {
                    if (typeof window[x_update_json.target] === 'function') {
                        window[x_update_json.target]( x_update_json.value );
                        continue;
                    }

                    var x_dest = x_update_json.target.split('.');
                    if (!x_element_map.has(x_dest[0])) {
                        x_element_map.set(x_dest[0], x_dest[0]);
                    }
                    dynamic_update(x_update_json);
                } catch(err) {
                    console.log("error " + err);
                }
            }

            // call update once per affected root element
            x_element_map.forEach((_value, key) => {
                eezz.on_update(key);
            })
        }

        // The backend might send events. The main event is the init-event, which is the response to the
        // initialization request. The idea is to put all long lasting methods into this loop, so that the
        // HTML page is not blocked at the first call.
        //if (x_json.event) {
        //    if (x_json.event == 'init') {
        //        for (x_element in x_list) {
        //        g_eezz_web_socket.send(x_element.getAttribute('data-eezz-init'));
        //    }
        //}
    }
}

// Dynamic update: The inner-HTML of the element is calculated by the server
// The result is send via WEB socket as json = {tag-name: html, ...}
function dynamic_update(a_update_json) {
    var x_dest      = a_update_json.target.split('.'); 
    var x_attr      = x_dest.pop();
    var x_elem_root = document.getElementById(x_dest[0]);
    var x_elem      = x_elem_root;

    if (x_attr == 'subtreeTemplate') {
        tree_expand(x_elem, a_update_json.value);
        return;
    }

    for (var i = 1; i < x_dest.length; i++) {
        x_elem = x_elem.getElementsByTagName(x_dest[i])[0];
    }

    if (x_elem == null) {
        console.log("warning: target not found " + a_update_json.target);
        return;
    }

    if (x_attr == 'innerHTML') {
        if (a_update_json.type == 'base64') {
            x_elem.innerHTML = window.atob(a_update_json.value);
        }
        else {
            x_elem.innerHTML = a_update_json.value;
        }
    }
    else if (x_attr == 'subtree') {
        tree_expand(x_elem, a_update_json.value)
    }
    else if (x_elem.tagName == 'IMG')
        x_elem.setAttribute(x_attr, 'data:image/png;base64,' + a_update_json.value);
    else {
        x_elem.setAttribute(x_attr, a_update_json.value);
    }    
}

// Collapse a tree element
function tree_collapse(a_node) {
    if (a_node.nextSibling) {
        if (a_node.nextSibling.getAttribute('data-eezz-subtree-id') == a_node.id) {
            a_node.nextSibling.remove();
        }
        // a_node.lastChild.remove();
    }
}

// Inserts a sub-tree into a tree <TR> element, which is defined a given element id
// The constrains are: subtree.tagName is table, and it contains a thead and a tbody
function tree_expand(a_node, subtree_descr) {
    // Create a new node
    if (!subtree_descr.template) {
        tree_collapse(a_node);
        return;
    }
    if (subtree_descr.tbody == '') {
        return;
    }

    var x_nr_cols       = a_node.getElementsByTagName('td').length.toString()
    var x_row           = document.createElement('tr');
    var x_td            = document.createElement('td');

    x_td.setAttribute('colspan', x_nr_cols+1);
    x_row.setAttribute('data-eezz-subtree-id', a_node['id']);
    x_row.appendChild(x_td);

    x_td.innerHTML      = subtree_descr.template;
    var x_table         = x_td.getElementsByTagName('table')[0];
    var x_caption       = x_td.getElementsByTagName('caption')[0];
    var x_thead         = x_td.getElementsByTagName('thead')[0];
    var x_tbody         = x_td.getElementsByTagName('tbody')[0];

    x_table.setAttribute('data-eezz-subtree-id',  a_node['id']);
    x_caption.remove();

    if (subtree_descr.option == 'restricted') {
        x_table.classList.add('clzz_node');
        x_tbody.classList.add('clzz_node');
        x_thead.remove();
        x_tbody.innerHTML = subtree_descr.tbody;
    }
    else {
        x_table.classList.add('clzz_node');
        x_tbody.classList.add('clzz_node');
        x_thead.innerHTML = subtree_descr.thead;

    }

    a_node.parentNode.insertBefore(x_row, a_node.nextSibling);
    a_node.setAttribute('data-eezz-subtree_state', 'expanded');

    // x_td = document.createElement('td');
    // x_td.classList.add('clzz_node_space')
    // x_td.style.width = '50px';
    // a_node.insertBefore(x_td, null);
}

// Function collects all eezz events from page using WEB-socket to
// send a request to the server
function eezzy_click(aEvent, aElement) {
    var x_post     = true;
    var x_response = "";
    var x_json     = JSON.parse(aElement.getAttribute('data-eezz-json'));

    if (!x_post) {
        return;
    }

    // Generated elements: Return without modifications
    if (aElement.hasAttribute('data-eezz-template')) {
        x_response = JSON.stringify(x_json);
        g_eezz_web_socket.send(x_response);
        return;
    }

    // User form elements: Collect the input data of this page.
    // The syntax for collection is as follows
    // function: <name>, args: { name1: "id-of-element"."attribute-of-element", name2:... }
    var x_function = x_json.call;
    if (x_function) {
        var x_args    = x_function.args;
        var x_element = document.getElementById(x_function.id);
        var x_attr;

        for (x_key in x_args) {
            var x_source = x_args[x_key];

            if (x_source.startsWith('[')) {
                var x_elem_list;
                var x_elem;
                var x_row_len = 0;
                var x_index;

                x_source    = x_source.replace('template.', 'data-eezz-template=');
                x_elem_list = x_element.querySelectorAll(x_source);

                for (var i = 0; i < x_elem_list.length; i++) {
                    x_elem    = x_elem_list[i]
                    x_index   = parseInt(x_elem.getAttribute('data-eezz-index'));
                    x_row_len = Math.max(x_row_len, x_index);
                }
                var x_new_row = new Array(x_row_len + 1);
                for (var i = 0; i < x_row_len + 1; i++) {
                    x_elem    = x_elem_list[i];
                    x_index   = parseInt(x_elem.getAttribute('data-eezz-index'));
                    x_new_row[x_index] = x_elem['value'];
                }
                x_json.call.args[x_key] = x_new_row;
            }
            else {
                x_attr = x_source.split('.');
                x_elem = document.getElementById(x_attr[0]);
                x_json.call.args[key] = x_elem.getAttribute(x_attr[1]);
            }
        }
        x_response = JSON.stringify(x_json);
        g_eezz_web_socket.send(x_response);
    }
}
