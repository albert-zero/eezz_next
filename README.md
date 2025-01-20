Graphical User Interface for Python
===================================

EEZZ provides you with a fast, easy to use and lightweight user interface.
- It is bidirectional and platform independent
- It replaces anything like JavaServerPages or PHP
- It is suitable for your playground sandbox as well as for business applications

The huge features list includes
- Tree, Grid and Input-Form Views
- Background push service
- Extended file download features
- Possible transparent access to SQLite
- Possible python loguru output for the actual request cycle

The installation comes with zero administration and works right out of the box.
The package uses python integrated HTML server as default. 
After installation, you could start the server with the following command to activate the framework.

**python -m eezz.server**

The server starts a bootstrap and creates the directory eezz/webroot with the necessary scripts and 
an example to start with. Now you could add your projects in eezz/webroot/applications.

Find an introduction and the documentation under the following links

- http://eezz.biz/eezz.pdf
- http://eezz.biz/index.html

To work more professional, try to work with https://nginx.org HTTP server and copy the content of ./eezz/wesocket to /var/www/html. 
Activate the nginx WebSocket interface (see https://nginx.org/en/docs/http/websocket.html)
 and execute 

**python -m eezz.server --webroot /var/www/html** 


