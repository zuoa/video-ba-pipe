"""Entrypoint for the worker-container interactive algorithm test service."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.algorithm_test_service import run_algorithm_test_server


if __name__ == "__main__":
    run_algorithm_test_server()
