#!/usr/bin/env python3
"""Entry point for the Flask web application"""

from app import create_app
import logging
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

if __name__ == '__main__':
    app = create_app()
    config = app.config['APP_CONFIG']

    host = config['web']['host']
    port = config['web']['port']

    # Debug mode exposes the Werkzeug debugger; never enable it when bound to
    # 0.0.0.0 (reachable by the whole tailnet). Opt in with FLASK_DEBUG=1.
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'

    print(f"Starting RSS Processor web interface on {host}:{port}")
    app.run(host=host, port=port, debug=debug, threaded=True)
