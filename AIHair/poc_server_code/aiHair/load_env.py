from dotenv import load_dotenv
import os

__env_before = os.environ.copy()
load_dotenv('.env.default')
