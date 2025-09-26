#!/usr/bin/env python3
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Test environment variables
bot_token = os.getenv("BOT_TOKEN")
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
password = os.getenv("PASSWORD")

# Write to file instead of stdout
with open("bot_test_output.txt", "w") as f:
    f.write(f"BOT_TOKEN: {bot_token}\n")
    f.write(f"API_ID: {api_id}\n")
    f.write(f"API_HASH: {api_hash}\n")
    f.write(f"PASSWORD: {password}\n")
    
    if bot_token and api_id and api_hash and password:
        f.write("All environment variables loaded successfully!\n")
    else:
        f.write("Some environment variables are missing!\n")

print("Test completed - check bot_test_output.txt")

