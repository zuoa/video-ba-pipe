"""Entrypoint for the worker-container local shared inference service."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.shared_inference import run_server


if __name__ == "__main__":
    run_server()

