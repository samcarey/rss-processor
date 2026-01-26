#!/usr/bin/env python3
"""Entry point for the background worker daemon"""

from daemon.worker import run_worker

if __name__ == '__main__':
    run_worker()
