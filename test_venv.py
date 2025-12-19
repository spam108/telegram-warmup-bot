#!/usr/bin/env python3
import os
import sys

# Test if we can write to a file
with open("venv_test.txt", "w") as f:
    f.write("Virtual environment is working!\n")
    f.write(f"Python version: {sys.version}\n")
    f.write(f"Current directory: {os.getcwd()}\n")
    f.write(f"Environment variables loaded: {len(os.environ)}\n")

print("Test completed - check venv_test.txt")

