Graphical User Interface for Python
===================================

The package uses python integrated HTML server as default. 
After installation, you could start the server with the following command to activate the framework.

**python -m eezz.server**

The server starts a bootstrap and creates the directory eezz/webroot with the necessary scripts and 
an example to start with. Now you could add your projects in eezz/webroot/applications.

Find an introduction and the documentation under the following links

- https://github.com/albert-zero/eezz_next/blob/main/webroot/applications/docs/eezz.pdf
- http://eezz.biz/index.html

To work with nginx HTTP server, copy the content of eezz/wesocket to /var/www/html
and activate the WebSocket interface: 
https://nginx.org/en/docs/http/websocket.html


