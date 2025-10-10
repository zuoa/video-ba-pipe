import os

from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# Database configuration
DB_PATH = os.getenv('DB_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data/db/ba.db'))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

FRAME_SAVE_PATH = os.getenv('FRAME_SAVE_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data/frames'))
os.makedirs(FRAME_SAVE_PATH, exist_ok=True)
