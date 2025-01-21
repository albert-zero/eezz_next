Graphical User Interface for Python
===================================

EEZZ provides your application with a fast, easy to use and lightweight user interface.
- It is bidirectional  
- It is platform independent
- It is open source

The huge features list includes
- Tree, Grid and Input-Form Views
- Push Service
- Extended File Download
- Transparent Access to SQLite

Find an introduction and the documentation under the following links

- http://eezz.biz/eezz.pdf
- http://eezz.biz/index.html

The installation comes with zero administration and works right out of the box.
The package uses python integrated HTML server as default. 
After installation, you could start the server with the following command to activate the framework.

**python -m eezz.server**

The server starts a bootstrap and creates the local directory eezz/webroot with the necessary scripts and 
an example to start with. Now you could open http://localhost:8000,
and start your journey, visiting the examples and creating your own modules in **eezz/webroot/applications**.

To work more professional, I would recommend using https://nginx.org HTTP server and to copy the content of ./eezz/wesocket to /var/www/html. 
Activate the nginx WebSocket interface (see https://nginx.org/en/docs/http/websocket.html)
 and execute the following line:

**python -m eezz.server --webroot /var/www/html** 

EEZZ is your highway from prototype to product. It ensures shorter development cycles,
easier ways to test your software and it encourages parallelizing your tasks. 


